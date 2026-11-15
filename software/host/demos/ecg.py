"""Annotated MIT-BIH heartbeat replay."""
from demos.replay import ReplayDemo


class Demo(ReplayDemo):
    def __init__(self, parent, app):
        super().__init__(parent, app, "ecg")
