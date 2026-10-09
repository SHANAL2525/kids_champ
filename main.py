import sys
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from app.config import APP_ICON, APP_NAME, VERSION
from app.ui.main_window import MainWindow


def main():
    app=QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setWindowIcon(QIcon(str(APP_ICON)))
    app.setStyle('Fusion')
    win=MainWindow(); win.show(); sys.exit(app.exec())


if __name__=='__main__':main()
