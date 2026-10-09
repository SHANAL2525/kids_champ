from __future__ import annotations

import threading
import shutil
import zipfile
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QObject, QThread, QSize, QTimer
from PySide6.QtGui import QPixmap, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QFrame,QLabel,QPushButton,QLineEdit,QComboBox,QFileDialog,
    QProgressBar,QScrollArea,QApplication
)
from PIL import Image

from ..batch_service import BatchCancelled, BatchSession
from ..config import MAX_BATCH_IMAGES
from ..image_renderer import render_item, output_filename
from ..models import BatchItem
from .dialogs import MessageDialog
from ..widgets.batch_table import BatchTable
from ..preview_service import PreviewTask, PreviewPool
from ..settings_store import load_settings, save_settings


class DropArea(QFrame):
    files_requested=Signal(object); browse_requested=Signal()
    def __init__(self,parent=None):
        super().__init__(parent); self.setAcceptDrops(True); self.setObjectName('Card'); self.setMinimumHeight(390)
        l=QVBoxLayout(self); l.setContentsMargins(6,6,6,6); l.setSpacing(5)
        self.preview=QLabel('+\n\nDrop ZIP or image files here\nor click to browse\n\nPNG, JPG, TIFF, WEBP, BMP · Maximum 250 artworks'); self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter); self.preview.setStyleSheet('font-size:16px;font-weight:600;')
        l.addWidget(self.preview,1); self.info=QLabel(''); self.info.setAlignment(Qt.AlignmentFlag.AlignCenter); self.info.setObjectName('Muted'); l.addWidget(self.info)
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.browse_requested.emit()
        super().mousePressEvent(event)
    def dragEnterEvent(self,event:QDragEnterEvent):
        urls=event.mimeData().urls()
        supported=('.zip','.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff')
        if urls and all(u.toLocalFile().lower().endswith(supported) for u in urls):event.acceptProposedAction()
    def dropEvent(self,event:QDropEvent):
        paths=[Path(u.toLocalFile()) for u in event.mimeData().urls()]
        if paths:self.files_requested.emit(paths)
    def set_preview(self,pix:QPixmap|None,text=''):
        if pix and not pix.isNull():
            self.preview.setText(''); self.preview.setPixmap(pix.scaled(QSize(max(10,self.preview.width()-8), max(10,self.preview.height()-8)),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        else:self.preview.setPixmap(QPixmap()); self.preview.setText('+\n\nDrop ZIP or image files here\nor click to browse\n\nPNG, JPG, TIFF, WEBP, BMP · Maximum 250 artworks')
        self.info.setText(text)


class LoadZipWorker(QObject):
    finished = Signal(object, object)  # Path, list[BatchItem]
    failed = Signal(object, str)      # Path, error

    def __init__(self, session: BatchSession, path: Path):
        super().__init__()
        self.session = session
        self.path = Path(path)
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            items = self.session.add_zip(self.path, self._cancel)
            self.finished.emit(self.path, items)
        except Exception as exc:
            self.failed.emit(self.path, str(exc))


class LoadImagesWorker(QObject):
    finished=Signal(object,object); failed=Signal(object,str)
    def __init__(self,session:BatchSession,paths:list[Path]):
        super().__init__(); self.session=session; self.paths=list(paths); self._cancel=threading.Event()
    def cancel(self):self._cancel.set()
    def run(self):
        try:self.finished.emit(self.paths,self.session.add_images(self.paths,self._cancel))
        except Exception as exc:self.failed.emit(self.paths,str(exc))


class GenerateWorker(QObject):
    progress=Signal(int,int); finished=Signal(str,str); failed=Signal(str); cancelled=Signal()

    def __init__(self,items:list[BatchItem],out_dir:Path,output_format:str="PNG"):
        super().__init__(); self.items=items; self.out_dir=out_dir; self.output_format=str(output_format or "PNG").upper(); self._cancel=threading.Event()

    def cancel(self):
        self._cancel.set()

    @staticmethod
    def save_output(image: Image.Image, path: Path, output_format: str = "PNG"):
        """Save PNG with alpha, or a viewer-safe white-background RGB TIFF."""
        rgba = image.convert("RGBA")
        if str(output_format).upper() == "TIFF":
            path = path.with_suffix(".tif")
            white = Image.new("RGB", rgba.size, (255, 255, 255))
            white.paste(rgba.convert("RGB"), mask=rgba.getchannel("A"))
            white.save(path, format="TIFF", compression="tiff_lzw")
        else:
            path = path.with_suffix(".png")
            rgba.save(path, format="PNG", optimize=True)
        return path

    @staticmethod
    def _remove_partial(folder: Path | None, zip_path: Path | None):
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)
        if zip_path is not None:
            try:
                zip_path.unlink(missing_ok=True)
            except OSError:
                pass

    def run(self):
        try:
            stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]
            folder=self.out_dir/f'KidsChamp_Output_{stamp}'
            zip_path=self.out_dir/f'KidsChamp_Output_{stamp}.zip'
            folder.mkdir(parents=True,exist_ok=True)
            used_by_dir={}
            files=[]
            total=len(self.items)
            jobs=[]
            completed=0
            for i,item in enumerate(self.items,1):
                if self._cancel.is_set():
                    raise BatchCancelled("Generation cancelled.")
                if item.status!='Ready':
                    completed += 1
                    self.progress.emit(completed,total)
                    continue

                rel = Path(item.source_relative) if item.source_relative else Path(item.filename)
                rel_parent = rel.parent
                target_dir = folder / rel_parent
                target_dir.mkdir(parents=True, exist_ok=True)

                ext = ".tif" if self.output_format == "TIFF" else ".png"
                used = used_by_dir.setdefault(rel_parent.as_posix().lower(), set())
                name = output_filename(item, used, ext)
                target = target_dir / name
                jobs.append((item,target))

            def render_job(job):
                if self._cancel.is_set():
                    raise BatchCancelled("Generation cancelled.")
                item,target=job
                img=render_item(item)
                if self._cancel.is_set():
                    raise BatchCancelled("Generation cancelled.")
                return self.save_output(img,target,self.output_format)

            workers=max(1,min(3,os.cpu_count() or 2,len(jobs)))
            with ThreadPoolExecutor(max_workers=workers,thread_name_prefix="kidschamp-render") as pool:
                futures=[pool.submit(render_job,job) for job in jobs]
                try:
                    for future in as_completed(futures):
                        files.append(future.result())
                        completed += 1
                        self.progress.emit(completed,total)
                except Exception:
                    self._cancel.set()
                    for future in futures: future.cancel()
                    raise

            if self._cancel.is_set():
                raise BatchCancelled("Generation cancelled.")
            with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as zf:
                for file_path in sorted(files,key=lambda p:p.as_posix().lower()):
                    if self._cancel.is_set():
                        raise BatchCancelled("Generation cancelled.")
                    zf.write(file_path, file_path.relative_to(folder).as_posix())
            self.finished.emit(str(folder),str(zip_path))
        except BatchCancelled:
            self._remove_partial(locals().get('folder'),locals().get('zip_path'))
            self.cancelled.emit()
        except Exception as exc:
            self._remove_partial(locals().get('folder'),locals().get('zip_path'))
            self.failed.emit(str(exc))


class GeneratorPage(QWidget):
    status=Signal(str)
    compare_requested=Signal(int)
    def __init__(self,parent=None):
        super().__init__(parent)
        self.settings=load_settings(); self.session=BatchSession(); self.items=self.session.items; self.current_index=-1; self.matches=[]; self.match_pos=-1; self.worker=None; self.thread=None; self.load_worker=None; self.load_thread=None; self.batch_mode='Auto'; self.batch_fit='Smart'
        self.preview_cache = {}
        self._preview_guard = False
        self._preview_request_id = 0
        self._preview_tasks = {}
        self._preview_active = False
        self._pending_preview = None
        self.search_timer = QTimer(self); self.search_timer.setSingleShot(True); self.search_timer.timeout.connect(self._apply_filter)
        self._build(); self._apply_filter()

    def _build(self):
        root=QVBoxLayout(self); root.setContentsMargins(18,12,18,12); root.setSpacing(10)
        body=QHBoxLayout(); body.setSpacing(12); root.addLayout(body,1)
        left=QVBoxLayout(); left.setSpacing(8); body.addLayout(left,46)
        hdr=QHBoxLayout(); title=QLabel('Selected output preview'); title.setObjectName('SectionTitle'); self.count=QLabel('0 artworks'); self.count.setObjectName('Muted'); hdr.addWidget(title); hdr.addStretch(1); hdr.addWidget(self.count)
        self.compare_preview_btn=QPushButton('Compare'); self.compare_preview_btn.setEnabled(False); self.compare_preview_btn.setToolTip('Open the selected artwork in Compare'); self.compare_preview_btn.clicked.connect(self.open_current_compare); hdr.addWidget(self.compare_preview_btn); left.addLayout(hdr)
        self.drop=DropArea(); self.drop.browse_requested.connect(self.browse_files); self.drop.files_requested.connect(self.add_files); left.addWidget(self.drop,1)
        thumbs_row=QHBoxLayout(); self.thumb_container=QWidget(); self.thumb_layout=QHBoxLayout(self.thumb_container); self.thumb_layout.setContentsMargins(0,0,0,0); self.thumb_layout.setSpacing(6)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(self.thumb_container); scroll.setFixedHeight(75); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded); scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff); thumbs_row.addWidget(scroll,1)
        self.add_more_btn=QPushButton('+ Add ZIP / Images'); self.add_more_btn.clicked.connect(self.browse_files); thumbs_row.addWidget(self.add_more_btn); left.addLayout(thumbs_row)

        right=QVBoxLayout(); right.setSpacing(8); body.addLayout(right,54)
        rh=QHBoxLayout(); bt=QLabel('Batch Preview'); bt.setObjectName('SectionTitle'); rh.addWidget(bt); rh.addStretch(1); self.match_label=QLabel('0 / 0'); prev=QPushButton('‹'); nxt=QPushButton('›'); prev.clicked.connect(lambda:self.nav_match(-1)); nxt.clicked.connect(lambda:self.nav_match(1)); rh.addWidget(prev); rh.addWidget(self.match_label); rh.addWidget(nxt); right.addLayout(rh)
        filters=QHBoxLayout(); self.search=QLineEdit(); self.search.setPlaceholderText('Search ID or name'); self.search.textChanged.connect(lambda _t:(self.search_timer.stop(),self.search_timer.start(120))); filters.addWidget(self.search,1)
        self.type_filter=QComboBox(); self.type_filter.addItems(['All types','Portrait','Landscape','Square']); self.type_filter.currentTextChanged.connect(self._apply_filter); filters.addWidget(self.type_filter)
        self.all_btn=QPushButton('All'); self.ready_btn=QPushButton('Ready'); self.error_btn=QPushButton('Errors'); self.status_filter='All'
        for b,n in [(self.all_btn,'All'),(self.ready_btn,'Ready'),(self.error_btn,'Errors')]: b.clicked.connect(lambda _=False,v=n:self.set_status_filter(v)); filters.addWidget(b)
        right.addLayout(filters)
        mode=QHBoxLayout(); mode.addWidget(QLabel('Template')); self.mode_buttons={}
        for name in ['Auto','Portrait','Landscape','Square']:
            b=QPushButton(name); b.clicked.connect(lambda _=False,v=name:self.set_batch_mode(v)); self.mode_buttons[name]=b; mode.addWidget(b)
        mode.addStretch(1); self.showing=QLabel('Showing 0 of 0'); self.showing.setObjectName('Muted'); mode.addWidget(self.showing); right.addLayout(mode)
        fitrow=QHBoxLayout(); fitrow.addWidget(QLabel('Artwork Fit')); self.fit_buttons={}
        for label,value in [('Smart','Smart'),('Full Artwork','Fit Full Artwork'),('Fill Frame','Fill Frame')]:
            b=QPushButton(label); b.clicked.connect(lambda _=False,v=value:self.set_batch_fit(v)); self.fit_buttons[value]=b; fitrow.addWidget(b)
        fitrow.addStretch(1); right.addLayout(fitrow)
        self.table=BatchTable(); self.table.preview_requested.connect(self.preview_index); self.table.edit_changed.connect(self.item_edited); self.table.delete_requested.connect(self.delete_item); self.table.compare_requested.connect(self.compare_requested.emit); right.addWidget(self.table,1)

        bottom=QHBoxLayout(); self.clear_btn=QPushButton('✕  Clear All'); self.clear_btn.setObjectName('Danger'); self.clear_btn.clicked.connect(self.clear_all); bottom.addWidget(self.clear_btn); bottom.addStretch(1)
        self.progress=QProgressBar(); self.progress.setFixedWidth(260); self.progress.hide(); bottom.addWidget(self.progress)
        self.cancel=QPushButton('Cancel'); self.cancel.clicked.connect(self.cancel_generation); self.cancel.setEnabled(False); bottom.addWidget(self.cancel)
        bottom.addWidget(QLabel('Output'))
        self.export_format=QComboBox(); self.export_format.addItems(['PNG','TIFF']); self.export_format.setToolTip('PNG preserves transparency; TIFF uses a solid white background for reliable viewing and printing'); self.export_format.setCurrentText(str(self.settings.get('export_format','PNG')).upper() if str(self.settings.get('export_format','PNG')).upper() in {'PNG','TIFF'} else 'PNG'); self.export_format.currentTextChanged.connect(self._export_format_changed); bottom.addWidget(self.export_format)
        self.download_current=QPushButton('Download Current'); self.download_current.setObjectName('Download'); self.download_current.clicked.connect(self.download_current_image); bottom.addWidget(self.download_current)
        self.download=QPushButton('⬇  Download All'); self.download.setObjectName('Download'); self.download.clicked.connect(self.download_all); bottom.addWidget(self.download); root.addLayout(bottom)
        self.set_batch_mode('Auto'); self.set_batch_fit('Smart'); self.set_status_filter('All')

    def browse_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,'Choose ZIP or artwork images','','Supported files (*.zip *.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff);;ZIP files (*.zip);;Images (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff)')
        if paths:self.add_files([Path(path) for path in paths])
    def add_files(self,paths):
        paths=[Path(path) for path in paths]; zips=[p for p in paths if p.suffix.lower()=='.zip']; images=[p for p in paths if p.suffix.lower()!='.zip']
        if (zips and images) or len(zips)>1:MessageDialog.info(self,'Choose one upload type','Drop one ZIP at a time, or select one/multiple image files together.')
        elif zips:self.add_zip(zips[0])
        elif images:self.add_images(images)
    def _export_format_changed(self,value):
        self.settings=load_settings(); self.settings['export_format']=str(value).upper(); save_settings(self.settings); self.status.emit('Output format: PNG (transparency preserved)' if str(value).upper() == 'PNG' else 'Output format: TIFF (solid white background)')
    def add_zip(self, path: Path):
        if self.load_thread is not None:
            self.status.emit('A ZIP is already loading…')
            return
        self.status.emit(f'Loading {path.name}…')
        self.add_more_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()

        self.load_thread = QThread(self)
        self.load_worker = LoadZipWorker(self.session, path)
        self.load_worker.moveToThread(self.load_thread)
        self.load_thread.started.connect(self.load_worker.run)
        self.load_worker.finished.connect(self._zip_loaded)
        self.load_worker.failed.connect(self._zip_load_failed)
        self.load_thread.start()

    def add_images(self,paths:list[Path]):
        if self.load_thread is not None:
            self.status.emit('Files are already loading…'); return
        self.status.emit(f'Loading {len(paths)} image(s)…'); self.add_more_btn.setEnabled(False); self.clear_btn.setEnabled(False)
        self.progress.setRange(0,0); self.progress.show(); self.load_thread=QThread(self); self.load_worker=LoadImagesWorker(self.session,paths)
        self.load_worker.moveToThread(self.load_thread); self.load_thread.started.connect(self.load_worker.run)
        self.load_worker.finished.connect(self._images_loaded); self.load_worker.failed.connect(self._images_load_failed); self.load_thread.start()

    def _finish_zip_thread(self):
        if self.load_thread is not None:
            self.load_thread.quit()
            self.load_thread.wait()
            self.load_thread.deleteLater()
        self.load_thread = None
        self.load_worker = None
        self.add_more_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)
        if self.worker is None:
            self.progress.hide()
        self.progress.setRange(0, 100)

    def _zip_loaded(self, path, new_items):
        self._finish_zip_thread()
        for item in new_items:
            item.template_mode = self.batch_mode
            item.artwork_fit_override = self.batch_fit
        self.items = self.session.items
        self._refresh_thumbs()
        self._apply_filter()
        self.count.setText(f'{len(self.items)} artworks · {len(self.session.zip_paths)} ZIPs')
        self.status.emit(f'Loaded {Path(path).name}')
        if self.items:
            self.compare_preview_btn.setEnabled(True)
            QTimer.singleShot(0, lambda: self.preview_index(0))

    def _zip_load_failed(self, path, message):
        self._finish_zip_thread()
        MessageDialog.info(self, 'Could not load ZIP', message)
        self.status.emit(f'Could not load {Path(path).name}')

    def _images_loaded(self,paths,new_items):
        self._finish_zip_thread()
        for item in new_items:item.template_mode=self.batch_mode; item.artwork_fit_override=self.batch_fit
        self.items=self.session.items; self._refresh_thumbs(); self._apply_filter(); self.count.setText(f'{len(self.items)} artworks · {len(self.session.zip_paths)} ZIPs')
        self.status.emit(f'Loaded {len(new_items)} image(s)')
        if self.items:self.compare_preview_btn.setEnabled(True); QTimer.singleShot(0,lambda:self.preview_index(len(self.items)-len(new_items)))

    def _images_load_failed(self,paths,message):
        self._finish_zip_thread(); MessageDialog.info(self,'Could not load images',message); self.status.emit('Could not load images')

    def _refresh_thumbs(self):
        while self.thumb_layout.count():
            it=self.thumb_layout.takeAt(0); w=it.widget();
            if w:w.deleteLater()
        for idx,item in enumerate(self.items[:8]):
            b=QPushButton(); b.setFixedSize(66,56); pix=QPixmap(str(item.source_path)); b.setIcon(QIcon(pix)); b.setIconSize(QSize(58,48)); b.clicked.connect(lambda _=False,i=idx:self.preview_index(i)); self.thumb_layout.addWidget(b)
        if len(self.items)>8:self.thumb_layout.addWidget(QLabel(f'+{len(self.items)-8}'))
        self.thumb_layout.addStretch(1)
    def _preview_key(self, item):
        return (
            str(item.source_path), item.submission_id, item.child_name, item.city,
            item.template_mode, item.artwork_fit_override,
            round(float(item.artwork_zoom), 4), round(float(item.artwork_offset_x), 2),
            round(float(item.artwork_offset_y), 2), int(item.artwork_rotation),
            bool(getattr(item, "auto_paper_detect", True)), int(getattr(item, "brightness", 0)),
            int(getattr(item, "contrast", 0)), int(getattr(item,"color_balance_red",0)),
            int(getattr(item,"color_balance_green",0)), int(getattr(item,"color_balance_blue",0)),
            int(getattr(item,"hue",0)), int(getattr(item,"saturation",0)), int(getattr(item,"lightness",0)),
            str(getattr(item, "preset_name", "None") or "None"),
            getattr(item, "manual_crop", None), getattr(item, "manual_warp", None),
            getattr(item, "stretch_strokes", None),
            getattr(item, "artwork_transform", None),
        )

    def _start_preview_task(self, request_id, item, key, idx):
        self._preview_active = True
        task = PreviewTask(request_id, item, max_width=1100)
        task.signals.finished.connect(self._preview_ready)
        self._preview_tasks[request_id] = (task, key, idx)
        PreviewPool.start(task)

    def preview_index(self, idx: int):
        if self._preview_guard or not (0 <= idx < len(self.items)):
            return
        self._preview_guard = True
        try:
            self.current_index = idx
            item = self.items[idx]
            self.table.select_index(idx)
            self._preview_request_id += 1
            request_id = self._preview_request_id
            self._pending_preview = None
            key = self._preview_key(item)
            pix = self.preview_cache.get(key)
            if pix is not None:
                self.drop.set_preview(pix, f'{item.child_name} — {item.city} · {item.selected_layout}')
                return

            self.drop.info.setText(f'Preparing preview…  {item.child_name} — {item.city}')
            if self._preview_active:
                self._pending_preview = (request_id, item, key, idx)
                return
            self._start_preview_task(request_id, item, key, idx)
        finally:
            self._preview_guard = False

    def _preview_ready(self, request_id, qimage, error):
        entry = self._preview_tasks.pop(request_id, None)
        self._preview_active = False
        if entry:
            _task, key, idx = entry
            if not error and qimage is not None:
                pix = QPixmap.fromImage(qimage)
                if len(self.preview_cache) >= 32:
                    self.preview_cache.pop(next(iter(self.preview_cache)))
                self.preview_cache[key] = pix
                if request_id == self._preview_request_id and idx == self.current_index:
                    item = self.items[idx]
                    self.drop.set_preview(pix, f'{item.child_name} — {item.city} · {item.selected_layout}')
            elif request_id == self._preview_request_id and idx == self.current_index:
                self.drop.info.setText(f'Preview error: {error}')

        pending = self._pending_preview
        self._pending_preview = None
        if pending is not None and pending[0] == self._preview_request_id:
            self._start_preview_task(*pending)

    def _pil_to_qimage(self,image:Image.Image):
        from PySide6.QtGui import QImage
        rgba=image.convert('RGBA'); data=rgba.tobytes('raw','RGBA'); return QImage(data,rgba.width,rgba.height,QImage.Format_RGBA8888).copy()
    def set_status_filter(self,value):self.status_filter=value; self._apply_filter()
    def set_batch_mode(self,value):
        self.batch_mode=value
        for item in self.items:item.template_mode=value
        for n,b in self.mode_buttons.items():b.setObjectName('SegmentActive' if n==value else 'SegmentInactive'); b.style().unpolish(b); b.style().polish(b)
        self._apply_filter();
        if self.current_index>=0:self.preview_index(self.current_index)
    def set_batch_fit(self,value):
        self.batch_fit=value
        for item in self.items:item.artwork_fit_override=value
        for n,b in self.fit_buttons.items():
            b.setObjectName('SegmentActive' if n==value else 'SegmentInactive'); b.style().unpolish(b); b.style().polish(b)
        self.preview_cache.clear()
        if self.current_index>=0:self.preview_index(self.current_index)
    def _apply_filter(self):
        if not hasattr(self,'table'):return
        query=self.search.text().strip().lower(); typ=self.type_filter.currentText(); visible=[]; self.matches=[]
        for i,item in enumerate(self.items):
            search_ok=not query or query in item.child_name.lower() or query in item.submission_id.lower()
            if query and search_ok:self.matches.append(i)
            type_ok=typ=='All types' or item.selected_layout==typ
            status_ok=self.status_filter=='All' or (self.status_filter=='Ready' and item.status=='Ready') or (self.status_filter=='Errors' and item.status!='Ready')
            if search_ok and type_ok and status_ok:visible.append(i)
        # Build the 250 rows only when the batch itself changes. Search/type/status
        # filters only hide/show existing rows, which avoids widget churn and lag.
        if self.table._items is not self.items or self.table.rowCount() != len(self.items):
            self.table.set_items(self.items, None)
        self.table.set_visible_indices(visible)
        self.showing.setText(f'Showing {len(visible)} of {len(self.items)}')
        self.match_pos=0 if self.matches else -1; self.match_label.setText(f'{1 if self.matches else 0} / {len(self.matches)}')
        for n,b in [('All',self.all_btn),('Ready',self.ready_btn),('Errors',self.error_btn)]:b.setObjectName('SegmentActive' if n==self.status_filter else 'SegmentInactive'); b.style().unpolish(b); b.style().polish(b)
    def nav_match(self,step):
        if not self.matches:return
        self.match_pos=(self.match_pos+step)%len(self.matches); idx=self.matches[self.match_pos]; self.match_label.setText(f'{self.match_pos+1} / {len(self.matches)}'); self.table.select_index(idx); self.preview_index(idx)
    def item_edited(self,idx):
        self.preview_cache.clear(); self.status.emit('Row updated automatically.');
        if idx==self.current_index:self.preview_index(idx)
    def delete_item(self,idx):
        if not (0<=idx<len(self.items)):return
        removed=self.items.pop(idx); self.preview_cache.clear(); self.current_index=-1; self._refresh_thumbs(); self._apply_filter(); self.count.setText(f'{len(self.items)} artworks · {len(self.session.zip_paths)} ZIPs'); self.status.emit(f'Removed {removed.filename}')
        if self.items:self.preview_index(min(idx,len(self.items)-1))
        else:
            self.drop.set_preview(None,'')
            self.compare_preview_btn.setEnabled(False)
    def clear_all(self):
        if self.load_thread is not None:
            MessageDialog.info(self, 'Please wait', 'The ZIP is still loading. Clear All will be available when loading finishes.')
            return
        if self.items and not MessageDialog.confirm(self,'Clear all?','Remove all uploaded artworks and current previews? Saved templates will stay.',primary='Clear All',secondary='Cancel',kind='danger'):
            return
        self._preview_guard = True
        try:
            self._preview_request_id += 1
            self.session.clear(); self.items=self.session.items; self.preview_cache.clear(); self.current_index=-1; self.matches=[]; self.match_pos=-1
            self.search_timer.stop()
            self.search.blockSignals(True); self.search.clear(); self.search.blockSignals(False)
            self.type_filter.blockSignals(True); self.type_filter.setCurrentText('All types'); self.type_filter.blockSignals(False)
            self.status_filter='All'; self.batch_mode='Auto'; self.batch_fit='Smart'
            for item in self.items: item.template_mode='Auto'
            for n,b in self.mode_buttons.items():
                b.setObjectName('SegmentActive' if n=='Auto' else 'SegmentInactive'); b.style().unpolish(b); b.style().polish(b)
            for n,b in self.fit_buttons.items():
                b.setObjectName('SegmentActive' if n=='Smart' else 'SegmentInactive'); b.style().unpolish(b); b.style().polish(b)
            self.table.set_items([], [])
            self._refresh_thumbs()
            self.count.setText('0 artworks')
            self.showing.setText('Showing 0 of 0')
            self.match_label.setText('0 / 0')
            self.drop.set_preview(None,'')
            self.compare_preview_btn.setEnabled(False)
            for n,b in [('All',self.all_btn),('Ready',self.ready_btn),('Errors',self.error_btn)]:
                b.setObjectName('SegmentActive' if n=='All' else 'SegmentInactive'); b.style().unpolish(b); b.style().polish(b)
            self.table.clearSelection(); self.table._icon_cache.clear(); self.table.viewport().update()
            self.thumb_container.adjustSize(); self.thumb_container.updateGeometry()
            self.drop.preview.clear(); self.drop.set_preview(None,'')
            self.drop.repaint(); self.table.repaint(); self.thumb_container.repaint(); self.repaint()
            QApplication.processEvents()
        finally:
            self._preview_guard = False
        self.status.emit('Current session cleared.')
    def download_all(self):
        ready=[i for i in self.items if i.status=='Ready']
        if not ready:MessageDialog.info(self,'Nothing to download','Upload a valid batch first.'); return
        folder=QFileDialog.getExistingDirectory(self,'Choose output folder')
        if not folder:return
        self.thread=QThread(self); self.worker=GenerateWorker(list(self.items),Path(folder),self.export_format.currentText()); self.worker.moveToThread(self.thread); self.thread.started.connect(self.worker.run); self.worker.progress.connect(self.on_progress); self.worker.finished.connect(self.on_finished); self.worker.failed.connect(self.on_failed); self.worker.cancelled.connect(self.on_cancelled); self.thread.start(); self.cancel.setEnabled(True); self.download.setEnabled(False); self.export_format.setEnabled(False); self.progress.setMaximum(len(self.items)); self.progress.setValue(0); self.progress.show(); self.status.emit(f'Generating {self.export_format.currentText()} outputs...')
    def download_current_image(self):
        if not (0<=self.current_index<len(self.items)) or self.items[self.current_index].status!='Ready':
            MessageDialog.info(self,'Nothing to download','Select a ready artwork first.'); return
        item=self.items[self.current_index]; fmt=self.export_format.currentText(); ext='.tif' if fmt=='TIFF' else '.png'; suggested=output_filename(item,set(),ext)
        path,_=QFileDialog.getSaveFileName(self,'Save current artwork',suggested,f'{fmt} files (*{ext})')
        if not path:return
        try:
            saved=GenerateWorker.save_output(render_item(item),Path(path),fmt); self.status.emit(f'Saved current artwork: {saved}'); MessageDialog.info(self,'Image saved',f'Current edited artwork saved.\n\n{saved}')
        except Exception as exc:MessageDialog.info(self,'Download failed',str(exc))
    def on_progress(self,current,total):self.progress.setMaximum(total); self.progress.setValue(current)
    def _stop_thread(self):
        self.cancel.setEnabled(False); self.download.setEnabled(True); self.export_format.setEnabled(True); self.progress.hide()
        if self.thread:self.thread.quit(); self.thread.wait(); self.thread.deleteLater(); self.thread=None
        self.worker=None
    def on_finished(self,folder,zip_path):
        self._stop_thread(); self.status.emit(f'Done: {zip_path}'); MessageDialog.info(self,'Batch complete',f'All final images are ready.\n\nZIP: {zip_path}')
    def on_failed(self,msg):self._stop_thread(); MessageDialog.info(self,'Generation failed',msg)
    def on_cancelled(self):self._stop_thread(); self.status.emit('Generation cancelled.')
    def cancel_generation(self):
        if self.worker:self.worker.cancel(); self.status.emit('Cancelling...')
    def templates_updated(self):
        self.preview_cache.clear()
        if self.current_index>=0:self.preview_index(self.current_index)
        self.status.emit('Generator reloaded latest template settings.')

    def open_current_compare(self):
        if self.items:
            idx = self.current_index if 0 <= self.current_index < len(self.items) else 0
            self.compare_requested.emit(idx)

    def refresh_after_compare(self, idx: int | None = None):
        """Refresh only what changed; never rebuild all 250 rows for one saved artwork."""
        self.preview_cache.clear()
        if self.items:
            target = idx if idx is not None and 0 <= idx < len(self.items) else (
                self.current_index if 0 <= self.current_index < len(self.items) else 0
            )
            # A search/filter may depend on edited ID/name. Only then do a full filter pass.
            if self.search.text().strip():
                self._apply_filter()
            elif idx is not None:
                self.table.refresh_index(target)
            self.preview_index(target)
            self.compare_preview_btn.setEnabled(True)
        else:
            self.drop.set_preview(None, '')
            self.compare_preview_btn.setEnabled(False)
        self.status.emit('Generator refreshed with Compare adjustments.')

    def shutdown(self, timeout_ms: int = 30000) -> bool:
        """Cooperatively stop workers; never delete their temp files while active."""
        if self.load_worker is not None:
            self.load_worker.cancel()
        if self.worker is not None:
            self.worker.cancel()
        ok = True
        for thread in (self.load_thread, self.thread):
            if thread is not None and thread.isRunning():
                thread.requestInterruption(); thread.quit()
                ok = bool(thread.wait(timeout_ms)) and ok
        if ok:
            self.session.close()
        return ok
