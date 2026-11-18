"""Frontend contracts, session failures, face geometry and real HTTP capture tests."""

import csv
import http.client
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

HOST=Path(__file__).resolve().parents[1]/'host'
sys.path.insert(0,str(HOST))
from demos.inference import InferenceSession, Prediction
from demos.phone_capture import CaptureServer, PAGE
from demos.runner import run
from demos.signals import AXES, golden_samples, har_stats, phone_csv, phone_windows, prepare_ecg, prepare_har
from demos.vision import decode_faces, face_crop, infer_image, letterbox, unmap_boxes
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

    def test_phone_follow_resumes_after_gap_and_ignores_partial_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'live.csv'
            with path.open('w', newline='') as handle:
                writer = csv.writer(handle)
                writer.writerow(('time_s', 'ax', 'ay', 'az', 'gx', 'gy', 'gz'))
                writer.writerows((i * .02, 0, 0, 9.80665, 0, 0, 0) for i in range(170))
                writer.writerows((i * .02 + .16, 0, 0, 9.80665, 0, 0, 0) for i in range(170, 400))
            with self.assertRaisesRegex(ValueError, '100 ms'):
                phone_csv(path, .455)
            with path.open('a') as handle:
                handle.write('8.16,0,0')
            with self.assertRaisesRegex(ValueError, 'CSV'):
                phone_csv(path, .455)
            samples = phone_csv(path, .455, live=True)
            self.assertGreater(samples[0].timestamp, 170 * .02 + .16)
            self.assertIsNotNone(samples[0].preprocess_ms)
            self.assertEqual(len(samples), 2)


class Sessions(unittest.TestCase):
    def test_demo_reports_transfer_and_compute_wait_with_the_production_driver(self):
        from hardware.npu.scripts.run_program_regression import Recorder
        from npu_program import encode_tensor

        session = InferenceSession(args('fpga'), 'ecg')
        sample = golden_samples('ecg')[0]
        transport = Recorder(session.package, encode_tensor(sample.expected_scores, 'i32'))
        transport.regs[0x38] = 500
        with patch('demos.inference.TclRpc'), patch('demos.inference.JtagTransport', return_value=transport):
            session.connect()
            try:
                prediction = session.infer(sample.values)
            finally:
                session.close()
        self.assertEqual(list(prediction.scores), sample.expected_scores)
        self.assertEqual(prediction.cycles, 500)
        self.assertGreater(prediction.transfer_ms, 0)
        self.assertGreater(prediction.compute_wait_ms, 0)
        self.assertLessEqual(prediction.transfer_ms + prediction.compute_wait_ms, prediction.elapsed_ms)
        self.assertEqual(sum(row.startswith('07 ') for row in transport.rows), 1)

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
    def test_shared_emotion_pipeline_uses_largest_face_and_loads_on_demand(self):
        image = Image.new('RGB', (160, 80))
        detector = Mock()
        detector.detect.return_value = []
        get_session = Mock()
        self.assertEqual(infer_image(image, 'emotion', detector, get_session)[:2], ([], None))
        get_session.assert_not_called()
        detector.detect.return_value = [[0, 0, 20, 20, .99], [20, 0, 80, 60, .9]]
        session = get_session.return_value
        session.scale = .25
        session.package.manifest = {'tensors': {'input': {'shape': [40, 40, 1]}}}
        boxes, prediction, timing = infer_image(image, 'emotion', detector, get_session, capture_ms=2)
        self.assertEqual(boxes, [detector.detect.return_value[1]])
        self.assertIs(prediction, session.infer.return_value)
        session.infer.assert_called_once_with([-4] * 1600, capture_ms=2, preprocess_ms=timing)

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
    @unittest.skipUnless(shutil.which('node'), 'Node is required to exercise the browser script')
    def test_browser_restart_waits_for_the_previous_upload_queue(self):
        script = PAGE.split('<script>', 1)[1].split('</script>', 1)[0]
        result = subprocess.run(['node', str(Path(__file__).with_name('check_demo_phone_capture.js'))],
                                input=script, encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

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
    @patch('demos.runner.make_session')
    def test_fpga_validation_requires_every_vector_from_every_demo(self, make_session):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('mnist', 'face'):
                model = root / name
                model.mkdir()
                frames = [dict(index=i, input=[i], output=[i]) for i in range(2)]
                (model / 'golden.json').write_text(json.dumps(dict(frames=frames)), encoding='utf-8')
            def make(args, name):
                return SimpleNamespace(directory=root / name, connect=lambda: None, close=lambda: None,
                                       infer=lambda values, **_: Prediction(tuple(values), 1),
                                       package=SimpleNamespace(image=b'model'))
            make_session.side_effect = make
            demos = {'mnist': None, 'face': None}
            for i, (name, limit, complete) in enumerate((('mnist', 1, False), ('mnist', 2, False),
                                                        (None, 1, False), (None, 2, True))):
                with self.subTest(name=name, limit=limit):
                    options = SimpleNamespace(input=None, output=root / f'report{i}.json', self_test=True,
                                              backend='fpga', demo=name, limit=limit)
                    self.assertTrue(run(options, demos))
                    report = json.loads(options.output.read_text(encoding='utf-8'))
                    self.assertEqual(report['fpga_validated'], complete)
                    self.assertEqual(report['golden_complete'], complete)
                    self.assertEqual(report['golden_coverage']['mnist'], dict(expected=2, checked=limit))

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
