"""Offscreen Qt acceptance for persistent guest-screen text and DJ logo layers.

Uses an isolated runtime, a generated transparent image and replay data only.
No DJ applications, system permissions or real customer screens are touched.
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtTest import QTest
from tools.ui_state_smoke import visual_items


def find(window, name):
    return next((item for item in visual_items(window.contentItem()) if item.objectName() == name and item.isVisible()), None)


def wait_for(condition, ms=4000):
    for _ in range(ms // 50):
        if condition():
            return True
        QTest.qWait(50)
    return condition()


def main():
    # Keep this existing entry point while exercising the current DJ workflow.
    from tools.check_screen_content import main as check
    check()


if __name__ == '__main__':
    main()
