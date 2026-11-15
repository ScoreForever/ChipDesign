from demos.camera import CameraDemo


class Demo(CameraDemo):
    def __init__(self,parent,app):
        super().__init__(parent,app,"face")
