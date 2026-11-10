"""Cloud streaming delivers all PDF bytes before temporary files are removed."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pymupdf
from fastapi.testclient import TestClient
import app
from cloud_stream import stream_results

class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rid = 'a' * 32
        self.child = 'b' * 32
        pdf = pymupdf.open();pdf.new_page();self.pdf = pdf.tobytes();pdf.close()
        self.root_patch = patch.object(app, 'RUNS', self.root)
        self.root_patch.start()
    def tearDown(self):
        self.root_patch.stop();self.temp.cleanup()
    def test_stream_delivers_original_and_masked_pdfs_then_removes_files(self):
        def pipeline(rid, data, filename, stage):
            stage(1)
            folder = self.root/self.child;folder.mkdir();(folder/'low.pdf').write_bytes(self.pdf)
            stage(2)
            return {'결합':True,'문서들':[{'등급':'중','문서종':'검증 문서','원본url':f'/runs/{rid}/input.pdf','마스킹본':[{'등급':'하','url':f'/runs/{self.child}/low.pdf'}]}]}
        with patch.object(app,'CLOUD',True),patch.object(app,'AGENT_GRADE','test'),patch.object(app,'AGENT_SPLIT','test'),patch.object(app,'pipeline',pipeline):
            with TestClient(app.app) as client:
                response=client.post('/api/process',files={'file':('test.pdf',self.pdf,'application/pdf')})
        self.assertIn('application/x-ndjson',response.headers['content-type'])
        events=[json.loads(line) for line in response.text.splitlines()]
        files=[x['file'] for x in events if 'file' in x]
        self.assertEqual(len(files),2)
        for item in files:
            self.assertEqual(base64.b64decode(item['pdf']),self.pdf)
            self.assertTrue(base64.b64decode(item['preview']).startswith(b'\x89PNG'))
        self.assertEqual(events[-1]['result']['문서들'][0]['문서종'],'검증 문서')
        self.assertTrue(events[-1]['done'])
        self.assertEqual(list(self.root.iterdir()),[])
    def test_stream_error_is_readable_and_cleans_original(self):
        (self.root/self.rid).mkdir();(self.root/self.rid/'input.pdf').write_bytes(self.pdf)
        with patch.object(app,'pipeline',side_effect=app.HTTPException(502,'외부 분석 오류')):
            events=[json.loads(line) for line in stream_results(app,self.rid,self.pdf,'test.pdf')]
        self.assertEqual(events[-1],{'error':'외부 분석 오류'})
        self.assertFalse((self.root/self.rid).exists())

if __name__=='__main__':unittest.main()
