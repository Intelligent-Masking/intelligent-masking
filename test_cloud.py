"""Cloud jobs survive separate requests without keeping a worker alive."""
import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pymupdf
from fastapi.testclient import TestClient
import app
import cloud_jobs

class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.root = Path(self.temp.name)
        pdf = pymupdf.open();pdf.new_page();self.pdf = pdf.tobytes();pdf.close()
        self.jobs={};self.deleted=[];self.uploads=0;self.pending=set();self.failed=set()
        self.responses=SimpleNamespace(create=self.create,retrieve=self.retrieve)
        files=SimpleNamespace(create=self.upload,delete=lambda fid:self.deleted.append(fid))
        client=SimpleNamespace(files=files,responses=self.responses)
        patches=[patch.object(app,'RUNS',self.root),patch.object(app,'CLOUD',True),patch.object(app,'client',lambda p=None:client),patch.object(app,'scope',lambda a:'project'),patch.object(app,'CONFIG',{}),patch.object(app,'AGENT_SPLIT','split'),patch.object(app,'AGENT_GRADE','grade'),patch.object(app,'AGENT_MASK_MID','mid'),patch.object(app,'AGENT_MASK_LOW','low'),patch.dict(os.environ,{'UPSTAGE_API_KEY':'test-secret'})]
        for p in patches:p.start();self.addCleanup(p.stop)
        self.addCleanup(self.temp.cleanup)
        self.web=TestClient(app.app)
    def upload(self,**kwargs):
        self.uploads+=1;return SimpleNamespace(id=f'file_{self.uploads}')
    def create(self,**kwargs):
        job=f'job_{len(self.jobs)}';self.jobs[job]=kwargs['model'];self.pending.add(job)
        return SimpleNamespace(id=job)
    def retrieve(self,job,**kwargs):
        model=self.jobs[job]
        if job in self.pending:
            self.pending.remove(job);data={'status':'in_progress','output':[]}
        elif model in self.failed:data={'status':'failed','output':[]}
        elif model=='split':data={'status':'completed','output':[{'model':'classify','step_type':'document-classify','content':[{'text':'검증 문서','additional_values':{}}]}]}
        elif model=='grade':data={'status':'completed','output':[{'model':'Instruct','content':[{'text':'| 상 | 15.5 | 개인정보 포함 |'}]}]}
        else:data={'status':'completed','output':[{'content':[{'text':base64.b64encode(self.pdf).decode()}]}]}
        return SimpleNamespace(model_dump=lambda:data)
    def start(self):
        response=self.web.post('/api/process',files={'file':('test.pdf',self.pdf,'application/pdf')})
        self.assertEqual(response.status_code,200);return response.json()
    def test_all_phases_resume_and_deliver_pdfs(self):
        result=self.start();state=cloud_jobs.decode(result['ticket'])
        # A new server instance can recover everything from the signed ticket and Upstage.
        app.JOBS.clear()
        for _ in range(20):
            kwargs={'data':{'ticket':result['ticket']}}
            if result.get('requiresFile'):kwargs['files']={'file':('test.pdf',self.pdf,'application/pdf')}
            response=self.web.post('/api/continue',**kwargs)
            self.assertEqual(response.status_code,200,response.text[:200])
            if 'application/x-ndjson' in response.headers['content-type']:break
            result=response.json()
        else:self.fail('never finished')
        events=[json.loads(line) for line in response.text.splitlines()]
        files=[x['file'] for x in events if 'file' in x]
        self.assertEqual(len(files),3)
        for item in files:
            self.assertEqual(base64.b64decode(item['pdf']),self.pdf)
            self.assertTrue(base64.b64decode(item['preview']).startswith(b'\x89PNG'))
        final=events[-1]['result'];self.assertEqual(final['등급'],'상');self.assertEqual(len(final['마스킹본']),2)
        self.assertEqual(set(self.deleted),{'file_1','file_2'})
        self.assertEqual(list(self.root.iterdir()),[])
        self.assertEqual(self.uploads,2)
    def test_unsupported_and_review_skip_masking(self):
        for review in (False, True):
            with self.subTest(review=review):
                self.jobs.clear();self.pending.clear()
                name='검증 문서' if review else app.OTHERS
                original=self.retrieve
                def special(job, **kwargs):
                    model=self.jobs[job]
                    if model=='split':
                        response={'status':'completed','output':[{'step_type':'document-classify','content':[{'text':name,'additional_values':{}}]}]}
                    elif model=='grade':
                        response={'status':'completed','output':[{'model':'Instruct - 검토필요','content':[{'text':'필수 항목 누락'}]}]}
                    else:return original(job,**kwargs)
                    return SimpleNamespace(model_dump=lambda:response)
                self.responses.retrieve=special
                result=self.start()
                for _ in range(10):
                    kwargs={'data':{'ticket':result['ticket']}}
                    if result.get('requiresFile'):kwargs['files']={'file':('test.pdf',self.pdf,'application/pdf')}
                    response=self.web.post('/api/continue',**kwargs)
                    self.assertEqual(response.status_code,200,response.text[:200])
                    if 'application/x-ndjson' in response.headers['content-type']:break
                    result=response.json()
                final=json.loads(response.text.splitlines()[-1])['result']
                self.assertTrue(final['검토필요' if review else '미지원'])
                self.assertNotIn('등급',final)
                self.assertFalse(set(self.jobs.values()) & {'mid','low'})
                self.assertEqual(list(self.root.iterdir()),[])
    def test_ticket_tampering_and_wrong_pdf_are_rejected(self):
        result=self.start();ticket=result['ticket']
        response=self.web.post('/api/continue',data={'ticket':ticket+'x'})
        self.assertEqual(response.status_code,400);self.assertEqual(self.deleted,[])
        response=self.web.post('/api/continue',data={'ticket':ticket},files={'file':('other.pdf',self.pdf+b'changed')})
        self.assertEqual(response.status_code,400);self.assertEqual(self.deleted,['file_1'])
    def test_twenty_minute_deadline_cleans_up(self):
        result=self.start();state=cloud_jobs.decode(result['ticket'])
        state['created']=time.time()-1201
        response=self.web.post('/api/continue',data={'ticket':cloud_jobs.encode(state)})
        self.assertEqual(response.status_code,408);self.assertIn('20분',response.json()['detail'])
        self.assertEqual(self.deleted,['file_1'])
    def test_provider_failure_cleans_up(self):
        result=self.start();self.pending.clear();self.failed.add('split')
        response=self.web.post('/api/continue',data={'ticket':result['ticket']})
        self.assertEqual(response.status_code,502);self.assertEqual(self.deleted,['file_1'])

if __name__=='__main__':unittest.main()
