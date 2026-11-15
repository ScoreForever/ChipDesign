"""Frontend contracts, session failures, face geometry and real HTTP capture tests."""

import csv
import http.client
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

HOST=Path(__file__).resolve().parents[1]/'host'
sys.path.insert(0,str(HOST))
from demos.inference import InferenceSession
from demos.phone_capture import CaptureServer
from demos.signals import AXES, golden_samples, har_stats, phone_windows, prepare_ecg, prepare_har
from demos.vision import decode_faces, face_crop, letterbox, unmap_boxes
from demos.mnist_input import prepare as prepare_digit
from demos.kws_audio import one_second, mfcc


def args(backend='reference'):
    return SimpleNamespace(backend=backend,host='127.0.0.1',port=6666,log=None)


class Frontends(unittest.TestCase):
    def test_digit_blank_multistroke_and_centering(self):
        from PIL import ImageDraw
        blank=Image.new('L',(280,280))
        self.assertIsNone(prepare_digit(blank,.2))
        draw=ImageDraw.Draw(blank)
        draw.line((20,20,20,130),fill=255,width=12)
        draw.line((20,130,80,130),fill=255,width=12)
        frame,values=prepare_digit(blank,.2)
        self.assertEqual(len(values),784)
        self.assertEqual(frame.size,(28,28))
        pixels=np.array(frame,dtype=float)
        yy,xx=np.indices(pixels.shape)
        self.assertLess(abs((xx*pixels).sum()/pixels.sum()-13.5),.6)
        self.assertLess(abs((yy*pixels).sum()/pixels.sum()-13.5),.6)

    def test_wav_frontend_shape_resampling_and_finite_silence(self):
        audio=one_second(np.zeros(24000),48000)
        self.assertEqual(audio.shape,(16000,))
        features=mfcc(audio)
        self.assertEqual(features.shape,(49,10))
        self.assertTrue(np.isfinite(features).all())

    def test_ecg_matches_training_formula(self):
        signal=np.sin(np.arange(600)/13)+np.arange(600)/500
        beat,values=prepare_ecg(signal,260,.45)
        expected=signal[164:420].reshape(128,2).mean(1)
        expected=((expected-expected.mean())/(expected.std()+1e-6)).astype(np.float32)
        np.testing.assert_array_equal(beat,expected)
        self.assertEqual(values,np.rint(expected/.45).clip(-8,7).astype(int).tolist())
        for peak in (0,500):
            with self.assertRaises(ValueError):
                prepare_ecg(signal,peak,.45)

    def test_har_time_major_axis_order_and_train_stats(self):
        mean,std=har_stats()
        window=np.tile(mean,(128,1))
        window[0]=mean+std*np.arange(-4,5)
        values=prepare_har(window,1.0)
        self.assertEqual(values[:9],list(range(-4,5)))
        self.assertEqual(values[9:],[0]*(127*9))
        with self.assertRaises(ValueError):
            prepare_har(window.T,1)
        window[0,0]=np.nan
        with self.assertRaises(ValueError):
            prepare_har(window,1)
        self.assertEqual(AXES[3:6],('body_gyro_x','body_gyro_y','body_gyro_z'))

    def test_phone_window_size_units_and_rejections(self):
        t=np.arange(0,6,.02)
        acc=np.column_stack((np.zeros(len(t)),np.zeros(len(t)),np.full(len(t),9.80665)))
        gyro=np.zeros_like(acc)
        samples=phone_windows(t,acc,gyro,.455)
        self.assertTrue(samples)
        self.assertEqual(samples[0].signal.shape,(128,9))
        np.testing.assert_allclose(samples[0].signal[:,8],1,atol=1e-4)
        np.testing.assert_allclose(samples[0].signal[:,:6],0,atol=1e-4)
        self.assertEqual(len(samples[0].values),1152)
        bad=t.copy();bad[30]=bad[29]
        with self.assertRaises(ValueError):
            phone_windows(bad,acc,gyro,.455)
        with self.assertRaises(ValueError):
            phone_windows(t,acc,gyro,.455,rotation=[[1,0,0],[0,1,0],[0,0,-1]])
        with self.assertRaises(ValueError):
            phone_windows(t*3,acc,gyro,.455)


class Sessions(unittest.TestCase):
    def test_published_model_files_match_release_hashes(self):
        for name in ('face','emotion'):
            directory=HOST/'demos/models'/name
            hashes=json.loads((directory/'SHA256SUMS.json').read_text(encoding='utf-8'))
            for relative,expected in hashes.items():
                with self.subTest(model=name,file=relative):
                    self.assertEqual(hashlib.sha256((directory/relative).read_bytes()).hexdigest(),expected)

    def test_integer_golden_and_no_fabricated_cycles(self):
        session=InferenceSession(args(),'ecg')
        try:
            session.connect()
            sample=golden_samples('ecg')[0]
            result=session.infer(sample.values)
            self.assertEqual(list(result.scores),sample.expected_scores)
            self.assertIsNone(result.cycles)
            self.assertIn('软件参考',result.timing_text())
            with self.assertRaises(ValueError):
                session.infer(sample.values[:-1])
            bad=sample.values.copy();bad[0]=99
            with self.assertRaises(ValueError):
                session.infer(bad)
        finally:
            session.close()
        with self.assertRaises(RuntimeError):
            session.infer(sample.values)

    @patch('demos.inference.TclRpc')
    @patch('demos.inference.Npu')
    def test_fpga_residency_and_timeout_are_not_replayed(self,npu_type,rpc_type):
        rpc_type.return_value.execute.return_value=''
        npu=npu_type.return_value
        npu.infer_values.return_value=[1,9,2,3,4]
        npu.read.return_value=13442
        session=InferenceSession(args('fpga'),'ecg')
        session.connect()
        sample=golden_samples('ecg')[0]
        a=session.infer(sample.values)
        b=session.infer(sample.values)
        self.assertEqual(a.cycles,13442)
        self.assertEqual(a.index,b.index)
        self.assertEqual(npu.load_model.call_count,1)
        self.assertEqual(npu.infer_values.call_count,2)
        npu.infer_values.side_effect=TimeoutError('status uncertain')
        with self.assertRaises(TimeoutError):
            session.infer(sample.values)
        self.assertEqual(npu.infer_values.call_count,3)
        self.assertEqual(npu.load_model.call_count,1)
        self.assertEqual(rpc_type.call_count,1)
        session.close()
        rpc_type.return_value.close.assert_called_once()

    @patch('demos.inference.TclRpc')
    @patch('demos.inference.Npu')
    def test_connect_failure_closes_socket(self,npu_type,rpc_type):
        rpc_type.return_value.execute.return_value=''
        npu_type.return_value.load_model.side_effect=RuntimeError('ABI mismatch')
        session=InferenceSession(args('fpga'),'ecg')
        with self.assertRaises(RuntimeError):
            session.connect()
        rpc_type.return_value.close.assert_called_once()
        self.assertIsNone(session.npu)


class FaceGeometry(unittest.TestCase):
    def test_dense_box_decode_and_bad_output(self):
        head=np.zeros((4,4,5));head[...,0]=-20
        head[1,2]=[8,.5,.5,.4,.4]
        boxes=decode_faces(head.ravel(),dict(grid=[4,4],output_scale=1.0),(100,200))
        self.assertEqual(len(boxes),1)
        np.testing.assert_allclose(boxes[0][:4],[42.5,35,82.5,115])
        with self.assertRaises(ValueError):
            decode_faces([0],dict(grid=[4,4],output_scale=1),(10,10))
        head[0,0,1]=np.inf
        with self.assertRaises(ValueError):
            decode_faces(head.ravel(),dict(grid=[4,4],output_scale=1),(10,10))

    def test_letterbox_preserves_coordinates_and_clips_padding(self):
        image=Image.new('RGB',(640,240))
        canvas,transform=letterbox(image,320)
        self.assertEqual(canvas.size,(320,320))
        mapped=unmap_boxes([np.array([80,100,240,220,.9],dtype=np.float32)],transform,image.size)
        np.testing.assert_allclose(mapped[0][:4],[160,0,480,240])
        self.assertFalse(unmap_boxes([[0,0,50,20,.9]],transform,image.size))
        self.assertEqual(face_crop(image,mapped[0]).height,240)
        json.dumps(mapped)  # OpenCV scalars must survive result/report export.


class PhoneCapture(unittest.TestCase):
    def test_real_http_auth_ordering_and_atomic_bad_packet(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'capture.csv'
            server=CaptureServer(('127.0.0.1',0),path,'test-token')
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            def post(rows,token='test-token',origin=None):
                headers={'X-Demo-Token':token,'Content-Type':'application/json'}
                if origin:headers['Origin']=origin
                client.request('POST','/samples',json.dumps(rows),headers)
                response=client.getresponse();response.read();return response.status
            try:
                rows=[[1,0,0,9.8,0,0,0],[1.02,0,0,9.8,0,0,0]]
                self.assertEqual(post(rows,'wrong'),403)
                self.assertEqual(post(rows,origin='http://other-host'),403)
                self.assertEqual(post(rows),200)
                self.assertEqual(post(rows),400)
                self.assertEqual(post([[2,0,0,9.8,0,0,0],[1.5,0,0,9.8,0,0,0]]),400)
                self.assertEqual(server.count,2)
                client.request('GET','/?token=wrong')
                response=client.getresponse();response.read();self.assertEqual(response.status,403)
            finally:
                client.close();server.shutdown();thread.join(3);server.server_close()
            with path.open() as handle:
                self.assertEqual(len(list(csv.DictReader(handle))),2)


class Headless(unittest.TestCase):
    def test_wrong_golden_fails_and_existing_report_is_preserved(self):
        frame=golden_samples('ecg')[0]
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'wrong.json'
            target=Path(directory)/'result.json'
            source.write_text(json.dumps(dict(input=frame.values,output=[0]*5)),encoding='utf-8')
            command=[sys.executable,str(HOST/'demo.py'),'ecg','--backend','reference','--input',str(source),'--output',str(target)]
            result=subprocess.run(command,cwd=directory,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,1)
            report=json.loads(target.read_text(encoding='utf-8'))
            self.assertFalse(report['passed'])
            self.assertFalse(report['runs'][0]['exact'])
            previous=target.read_bytes()
            result=subprocess.run(command,cwd=directory,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,1)
            self.assertEqual(target.read_bytes(),previous)


if __name__=='__main__':
    unittest.main()
