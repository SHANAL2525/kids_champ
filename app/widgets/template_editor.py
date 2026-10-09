from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, Signal, QPointF
from PySide6.QtGui import QPen, QColor, QBrush, QPixmap
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsSimpleTextItem, QGraphicsItem

from ..models import Box


class ResizeRectItem(QGraphicsRectItem):
    HANDLE=12
    def __init__(self, key:str, box:Box, color:str, callback):
        super().__init__(0,0,box.width,box.height); self.key=key; self.callback=callback; self.resizing=False
        self.setPos(box.x,box.y); self.setFlags(QGraphicsItem.ItemIsSelectable|QGraphicsItem.ItemIsMovable|QGraphicsItem.ItemSendsGeometryChanges); self.setAcceptHoverEvents(True)
        self.setPen(QPen(QColor(color),3,Qt.DashLine)); self.setBrush(QBrush(QColor(color + "22")))
        label=QGraphicsSimpleTextItem(key.replace('_box','').upper(),self); label.setBrush(QBrush(QColor(color))); label.setPos(4,4)

    def hoverMoveEvent(self,event):
        r=self.rect(); p=event.pos();
        self.setCursor(Qt.SizeFDiagCursor if abs(p.x()-r.right())<self.HANDLE and abs(p.y()-r.bottom())<self.HANDLE else Qt.SizeAllCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self,event):
        r=self.rect(); p=event.pos(); self.resizing=abs(p.x()-r.right())<self.HANDLE and abs(p.y()-r.bottom())<self.HANDLE
        if self.resizing: self._start=p; self._start_rect=QRectF(r); event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self.resizing:
            d=event.pos()-self._start; nr=QRectF(self._start_rect); nr.setWidth(max(20,nr.width()+d.x())); nr.setHeight(max(20,nr.height()+d.y())); self.setRect(nr); self.callback(self.key,self.to_box()); event.accept(); return
        super().mouseMoveEvent(event); self.callback(self.key,self.to_box())

    def mouseReleaseEvent(self,event):
        self.resizing=False; super().mouseReleaseEvent(event); self.callback(self.key,self.to_box())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            p=value; r=self.rect(); sr=self.scene().sceneRect(); x=max(0,min(p.x(),sr.width()-r.width())); y=max(0,min(p.y(),sr.height()-r.height())); return QPointF(x,y)
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.callback(self.key,self.to_box())
        return super().itemChange(change,value)

    def to_box(self):
        return Box(int(self.pos().x()),int(self.pos().y()),int(self.rect().width()),int(self.rect().height()))


class TemplateCanvas(QGraphicsView):
    box_changed=Signal(str,object)
    def __init__(self,parent=None):
        super().__init__(parent); self._scene=QGraphicsScene(self); self.setScene(self._scene); self.setDragMode(QGraphicsView.NoDrag); self.items_map={}
        self.setRenderHints(self.renderHints())

    def load(self,image_path, boxes:dict[str,Box]):
        self._scene.clear(); self.items_map={}; pix=QPixmap(str(image_path)); self._scene.addPixmap(pix); self._scene.setSceneRect(0,0,pix.width(),pix.height())
        colors={'artwork_box':'#16A34A','name_box':'#2563EB','city_box':'#F97316'}
        for key,box in boxes.items():
            item=ResizeRectItem(key,box,colors[key],lambda k,b:self.box_changed.emit(k,b)); self._scene.addItem(item); self.items_map[key]=item
        self.fitInView(self._scene.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if not self._scene.sceneRect().isEmpty():self.fitInView(self._scene.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)
