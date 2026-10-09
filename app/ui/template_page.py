from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QFrame,QLabel,QPushButton,QLineEdit,QComboBox,QSpinBox,QCheckBox,
    QFileDialog,QScrollArea,QColorDialog,QSplitter
)

from ..config import TEMPLATE_DIR, GROUP_COLORS
from ..models import TemplateRecord, Box
from ..template_store import (
    load_templates,load_groups,create_group,create_custom_template,duplicate_template,delete_template,set_locked,set_group,
    save_record,reveal_in_file_manager,LockedTemplateError,rename_group,delete_group,duplicate_group,set_group_locked,
    get_active_template_keys,set_active_template
)
from ..settings_store import load_settings, save_settings
from ..widgets.template_asset_view import TemplateAssetView
from ..widgets.template_editor import TemplateCanvas
from .dialogs import MessageDialog,GroupDialog,NewTemplateDialog


def _atomic_save_png(image: Image.Image, path: Path) -> None:
    temporary = path.with_name(path.name + ".tmp")
    image.convert("RGBA").save(temporary, format="PNG", optimize=True)
    os.replace(temporary, path)


class TemplatePage(QWidget):
    status=Signal(str); templates_changed=Signal()
    def __init__(self,parent=None):
        super().__init__(parent); self.records={}; self.current:TemplateRecord|None=None; self.saved_snapshot=None; self.undo=[]; self.dirty=False; self._updating=False
        self.settings=load_settings(); self.pending_image_source=None; self._build(); self.reload(); self.asset.set_mode(self.settings.get('template_view','grid'))

    def _build(self):
        root=QVBoxLayout(self); root.setContentsMargins(18,12,18,12); root.setSpacing(10)
        top=QHBoxLayout(); t=QLabel('Template Settings'); t.setObjectName('SectionTitle'); top.addWidget(t); top.addStretch(1); folder=QPushButton('Open template folder'); folder.clicked.connect(lambda:reveal_in_file_manager(TEMPLATE_DIR)); top.addWidget(folder); root.addLayout(top)
        split=QSplitter(Qt.Orientation.Horizontal); root.addWidget(split,1)
        left=QFrame(); left.setObjectName('Card'); ll=QVBoxLayout(left); ll.setContentsMargins(10,10,10,10); ll.addWidget(QLabel('Saved Templates'))
        active_hint=QLabel('● Active   ○ Inactive — click the circle/marker to use that template'); active_hint.setObjectName('Muted'); ll.addWidget(active_hint)
        self.asset=TemplateAssetView(); ll.addWidget(self.asset,1); split.addWidget(left)
        center=QFrame(); center.setObjectName('Card'); cl=QVBoxLayout(center); cl.setContentsMargins(10,10,10,10); ch=QHBoxLayout(); ch.addWidget(QLabel('Visual Editor')); ch.addStretch(1); self.unsaved=QLabel('Saved'); self.unsaved.setObjectName('Muted'); ch.addWidget(self.unsaved); cl.addLayout(ch)
        self.canvas=TemplateCanvas(); self.canvas.setMinimumWidth(520); self.canvas.box_changed.connect(self.box_changed); cl.addWidget(self.canvas,1); split.addWidget(center)
        right=QFrame(); right.setObjectName('Card'); rl=QVBoxLayout(right); rl.setContentsMargins(0,0,0,0)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); props=QWidget(); p=QVBoxLayout(props); p.setContentsMargins(14,12,14,12); p.setSpacing(8); scroll.setWidget(props); rl.addWidget(scroll); split.addWidget(right)
        split.setSizes([430,760,420]); split.setStretchFactor(0, 3); split.setStretchFactor(1, 6); split.setStretchFactor(2, 4)
        ph=QHBoxLayout(); lbl=QLabel('Template Properties'); lbl.setObjectName('SectionTitle'); ph.addWidget(lbl); ph.addStretch(1); self.edit_toggle=QPushButton('✎ Edit'); self.edit_toggle.clicked.connect(self.enable_properties); ph.addWidget(self.edit_toggle); p.addLayout(ph)
        p.addWidget(QLabel('Template name *')); self.name=QLineEdit(); p.addWidget(self.name)
        p.addWidget(QLabel('Template type *')); self.type=QComboBox(); self.type.addItems(['Portrait','Landscape','Square','Any']); p.addWidget(self.type)
        p.addWidget(QLabel('Group')); self.group=QComboBox(); p.addWidget(self.group)
        p.addWidget(QLabel('Artwork placement')); self.fit=QComboBox(); self.fit.addItems(['Fit Full Artwork','Fill Frame']); p.addWidget(self.fit)
        p.addWidget(QLabel('Template image *')); ir=QHBoxLayout(); self.image_path=QLineEdit(); self.image_path.setReadOnly(True); choose=QPushButton('+ Choose Image'); choose.clicked.connect(self.choose_image); ir.addWidget(self.image_path,1); ir.addWidget(choose); p.addLayout(ir); self.choose_btn=choose
        p.addWidget(QLabel('Editable boxes')); br=QHBoxLayout(); self.box_buttons={}
        for key,label in [('artwork_box','Artwork'),('name_box','Name'),('city_box','Location')]:
            b=QPushButton(label); b.clicked.connect(lambda _=False,k=key:self.select_box(k)); self.box_buttons[key]=b; br.addWidget(b)
        p.addLayout(br)
        p.addWidget(QLabel('Name font size')); self.name_size=QSpinBox(); self.name_size.setRange(14,160); p.addWidget(self.name_size)
        p.addWidget(QLabel('Location font size')); self.city_size=QSpinBox(); self.city_size.setRange(14,160); p.addWidget(self.city_size)
        p.addWidget(QLabel('Text colour')); cr=QHBoxLayout(); self.color=QLineEdit('#10344B'); color_btn=QPushButton('Choose'); color_btn.clicked.connect(self.choose_color); cr.addWidget(self.color); cr.addWidget(color_btn); p.addLayout(cr)
        self.pin=QCheckBox('Show location pin'); p.addWidget(self.pin)
        p.addWidget(QLabel('Location pin size')); self.pin_size=QSpinBox(); self.pin_size.setRange(12,120); p.addWidget(self.pin_size)
        p.addWidget(QLabel('Pin / text gap')); self.pin_gap=QSpinBox(); self.pin_gap.setRange(0,80); p.addWidget(self.pin_gap)
        self.lock=QCheckBox('Locked'); p.addWidget(self.lock); p.addStretch(1)
        for w in (self.name,self.type,self.group,self.fit,self.name_size,self.city_size,self.color,self.pin,self.pin_size,self.pin_gap,self.lock):
            if hasattr(w,'textChanged'): w.textChanged.connect(self.property_changed)
            if hasattr(w,'currentTextChanged'): w.currentTextChanged.connect(self.property_changed)
            if hasattr(w,'valueChanged'): w.valueChanged.connect(self.property_changed)
            if hasattr(w,'toggled'): w.toggled.connect(self.property_changed)
        bottom=QHBoxLayout(); self.undo_btn=QPushButton('↶ Undo'); self.undo_btn.clicked.connect(self.undo_change); bottom.addWidget(self.undo_btn); self.last_btn=QPushButton('Last Saved'); self.last_btn.clicked.connect(self.restore_last_saved); bottom.addWidget(self.last_btn); bottom.addStretch(1); self.save_btn=QPushButton('Save Changes'); self.save_btn.setObjectName('Primary'); self.save_btn.clicked.connect(self.save_current); bottom.addWidget(self.save_btn); root.addLayout(bottom)
        self.asset.new_group_requested.connect(self.new_group); self.asset.new_template_requested.connect(self.new_template); self.asset.edit_requested.connect(self.open_template); self.asset.delete_requested.connect(self.delete_records); self.asset.duplicate_requested.connect(self.duplicate_records); self.asset.lock_requested.connect(self.lock_records); self.asset.reveal_requested.connect(self.reveal_record); self.asset.move_requested.connect(self.move_records); self.asset.selection_changed.connect(self.selection_changed); self.asset.mode_changed.connect(self.view_mode_changed); self.asset.group_edit_requested.connect(self.edit_group); self.asset.group_delete_requested.connect(self.remove_group); self.asset.group_duplicate_requested.connect(self.duplicate_group_action); self.asset.group_lock_requested.connect(self.lock_group_action); self.asset.active_requested.connect(self.activate_template)

    def reload(self,select_key=None):
        records=load_templates(); self.records={r.key:r for r in records}; groups=load_groups()
        # Populate group choices before any template selection signal loads properties.
        self.group.blockSignals(True); current_group=self.group.currentText(); self.group.clear(); names=[g['name'] for g in groups]; self.group.addItems(names + (['Ungrouped'] if 'Ungrouped' not in names else []))
        if current_group and current_group in [self.group.itemText(i) for i in range(self.group.count())]: self.group.setCurrentText(current_group)
        self.group.blockSignals(False)
        self.asset.set_data(records,groups,get_active_template_keys())
        desired = select_key if select_key in self.records else (self.current.key if self.current and self.current.key in self.records else None)
        if desired is None and records:
            default = next((r for r in records if r.is_default and r.template_type == 'Landscape'), None) or next((r for r in records if r.is_default), None) or records[0]
            desired = default.key
        if desired:
            self.asset.select_key(desired)
            if not self.current or self.current.key != desired:
                self.open_template(desired)
    def activate_template(self, key):
        record = self.records.get(key)
        if not record:
            return
        try:
            active = set_active_template(record)
        except Exception as exc:
            MessageDialog.info(self, 'Could not activate template', str(exc))
            return
        self.reload(select_key=key)
        if record.template_type == 'Any':
            detail = 'Portrait + Landscape + Square'
        else:
            detail = record.template_type
        self.status.emit(f'Active {detail} template: {record.name}')
        self.templates_changed.emit()

    def selection_changed(self,keys):
        # Single-clicking a template must immediately show its real image and properties.
        if len(keys) != 1:
            return
        key = keys[0]
        if self.current and self.current.key == key:
            return
        self.open_template(key)
    def view_mode_changed(self,mode):
        self.settings=load_settings(); self.settings['template_view']=mode; save_settings(self.settings)
    def new_group(self):
        existing=load_groups(); default='New Group' if not any(g['name']=='New Group' for g in existing) else 'New Group 2'; dlg=GroupDialog(self,default)
        if dlg.exec():
            g=create_group(dlg.name.text(),dlg.color.currentText()); self.reload(); self.status.emit(f"Created group: {g['name']}")
    def edit_group(self, group_name):
        if group_name.lower() in {'defaults','ungrouped'}:
            MessageDialog.info(self, 'Protected group', 'Defaults and Ungrouped are system sections and cannot be renamed.')
            return
        groups = load_groups()
        current = next((g for g in groups if g['name'] == group_name), {'name': group_name, 'color': 'Blue'})
        dlg = GroupDialog(self, current['name'], current.get('color', 'Blue'), title='Edit Group', action_text='Save')
        if dlg.exec():
            try:
                new_name = rename_group(group_name, dlg.name.text(), dlg.color.currentText())
            except Exception as exc:
                MessageDialog.info(self, 'Could not edit group', str(exc)); return
            self.reload(); self.templates_changed.emit(); self.status.emit(f'Updated group: {new_name}')

    def remove_group(self, group_name):
        if group_name.lower() in {'defaults','ungrouped'}:
            MessageDialog.info(self, 'Protected group', 'Defaults and Ungrouped are system sections and cannot be deleted.')
            return
        if not MessageDialog.confirm(self, 'Delete group?', f'Delete the group “{group_name}”? Templates inside it will move to Ungrouped.', primary='Delete Group', secondary='Cancel', kind='danger'):
            return
        try:
            delete_group(group_name, 'Ungrouped')
        except Exception as exc:
            MessageDialog.info(self, 'Could not delete group', str(exc)); return
        self.reload(); self.templates_changed.emit(); self.status.emit(f'Deleted group: {group_name}')
    def duplicate_group_action(self, group_name):
        try:
            created = duplicate_group(group_name)
        except Exception as exc:
            MessageDialog.info(self, 'Could not duplicate group', str(exc)); return
        self.reload(); self.templates_changed.emit(); self.status.emit(f"Duplicated group: {created['name']}")

    def lock_group_action(self, group_name, value):
        try:
            count = set_group_locked(group_name, value)
        except Exception as exc:
            MessageDialog.info(self, 'Could not update group lock', str(exc)); return
        self.reload(); self.templates_changed.emit(); self.status.emit(f"{'Locked' if value else 'Unlocked'} {count} template(s) in {group_name}.")

    def new_template(self, group_hint='Ungrouped'):
        group_records = [g for g in load_groups() if str(g.get('name','')).lower() != 'defaults']
        groups = [g['name'] for g in group_records]
        if 'Ungrouped' not in groups:
            groups.append('Ungrouped')
        default_group = group_hint if group_hint in groups and str(group_hint).lower() != 'defaults' else 'Ungrouped'
        dlg=NewTemplateDialog(self,groups,default_group=default_group)
        if not dlg.exec():return
        selected_group = dlg.group.currentText() or 'Ungrouped'
        color = next((g.get('color','Blue') for g in group_records if g.get('name') == selected_group), 'Blue')
        rec=create_custom_template(dlg.name.text().strip(),dlg.type.currentText(),dlg.image_path,selected_group,color)
        self.reload(rec.key); self.enable_properties(); self.status.emit(f'New template created in {selected_group}. Adjust the three boxes, then save.')
    def open_template(self,key):
        if self.dirty and not MessageDialog.confirm(self,'Unsaved changes','Discard unsaved template changes and open another template?',primary='Discard',secondary='Cancel',kind='danger'):return
        rec=self.records.get(key); 
        if not rec:return
        self.current=deepcopy(rec); self.saved_snapshot=deepcopy(rec); self.undo=[]; self.dirty=False; self.pending_image_source=None; self._load_fields(); self.set_properties_enabled(False); self.unsaved.setText('Saved'); self.canvas.load(self.current.template_image,{'artwork_box':self.current.artwork_box,'name_box':self.current.name_box,'city_box':self.current.city_box})
    def _load_fields(self):
        if not self.current:return
        self._updating=True; r=self.current
        self.name.setText(r.name); self.type.setCurrentText(r.template_type)
        self.group.clear()
        if r.is_default:
            self.group.addItem('Defaults'); self.group.setCurrentText('Defaults')
        else:
            group_names=[g['name'] for g in load_groups() if str(g.get('name','')).lower() != 'defaults']
            if 'Ungrouped' not in group_names: group_names.append('Ungrouped')
            self.group.addItems(group_names)
            target=r.group if r.group in group_names and r.group.lower() != 'defaults' else 'Ungrouped'
            self.group.setCurrentText(target); r.group=target
        self.fit.setCurrentText(r.artwork_fit_mode); self.image_path.setText(str(r.template_image)); self.name_size.setValue(r.name_font_size); self.city_size.setValue(r.city_font_size); self.color.setText(r.text_color); self.pin.setChecked(r.show_location_pin); self.pin_size.setValue(r.location_pin_size); self.pin_gap.setValue(r.location_pin_gap); self.lock.setChecked(True if r.is_default else r.locked); self._updating=False
    def set_properties_enabled(self,enabled):
        for w in (self.name,self.fit,self.choose_btn,self.name_size,self.city_size,self.color,self.pin,self.pin_size,self.pin_gap):
            w.setEnabled(enabled)
        # Default Portrait/Landscape/Square are permanent production routing anchors.
        core_default = bool(self.current and self.current.is_default)
        self.type.setEnabled(enabled and not core_default)
        self.group.setEnabled(enabled and not core_default)
        self.lock.setEnabled(enabled and not core_default)
        self.canvas.setEnabled(enabled)
    def enable_properties(self):
        if self.current:self.set_properties_enabled(True); self.status.emit('Template editing enabled.')
    def _push_undo(self):
        if self.current:self.undo.append(deepcopy(self.current)); self.undo=self.undo[-30:]
    def property_changed(self,*_):
        if self._updating or not self.current:return
        self._push_undo(); r=self.current; r.name=self.name.text().strip(); r.template_type=self.type.currentText(); r.group=self.group.currentText(); r.artwork_fit_mode=self.fit.currentText(); r.name_font_size=self.name_size.value(); r.city_font_size=self.city_size.value(); r.text_color=self.color.text().strip() or '#10344B'; r.show_location_pin=self.pin.isChecked(); r.location_pin_size=self.pin_size.value(); r.location_pin_gap=self.pin_gap.value(); r.locked=self.lock.isChecked();
        if r.is_default:
            r.group='Defaults'; r.locked=True
        elif r.group.lower() == 'defaults':
            r.group='Ungrouped'
        self.mark_dirty()
    def box_changed(self,key,box):
        if not self.current:return
        setattr(self.current,key,box); self.mark_dirty()
    def select_box(self,key):
        item=self.canvas.items_map.get(key)
        if item:item.setSelected(True); self.canvas.centerOn(item)
    def choose_color(self):
        if not self.current:return
        c=QColorDialog.getColor(QColor(self.color.text()),self,'Choose text colour')
        if c.isValid():self.color.setText(c.name())
    def choose_image(self):
        if not self.current:return
        p,_=QFileDialog.getOpenFileName(self,'Choose template image','','Images (*.png *.jpg *.jpeg *.tif *.tiff *.webp *.bmp)')
        if not p:return
        self._push_undo(); draft=self.current.folder/'draft_template.png'; Image.open(p).convert('RGBA').save(draft); self.pending_image_source=draft; self.current.template_image=draft; self.image_path.setText(str(draft)); w,h=Image.open(draft).size; self.current.artwork_box=Box(int(w*.1),int(h*.1),int(w*.8),int(h*.58)); self.current.name_box=Box(int(w*.18),int(h*.72),int(w*.64),int(h*.08)); self.current.city_box=Box(int(w*.24),int(h*.81),int(w*.52),int(h*.07)); self.canvas.load(draft,{'artwork_box':self.current.artwork_box,'name_box':self.current.name_box,'city_box':self.current.city_box}); self.mark_dirty()
    def mark_dirty(self):self.dirty=True; self.unsaved.setText('Unsaved changes')
    def undo_change(self):
        if not self.undo:return
        self.current=self.undo.pop(); self._load_fields(); self.canvas.load(self.current.template_image,{'artwork_box':self.current.artwork_box,'name_box':self.current.name_box,'city_box':self.current.city_box}); self.mark_dirty()
    def restore_last_saved(self):
        if not self.saved_snapshot:return
        if self.pending_image_source and self.pending_image_source.exists(): self.pending_image_source.unlink(missing_ok=True)
        self.pending_image_source=None
        self.current=deepcopy(self.saved_snapshot); self._load_fields(); self.canvas.load(self.current.template_image,{'artwork_box':self.current.artwork_box,'name_box':self.current.name_box,'city_box':self.current.city_box}); self.dirty=False; self.unsaved.setText('Saved')
    def _validate(self):
        bad=[]
        for widget,ok,label in [(self.name,bool(self.name.text().strip()),'Template name'),(self.type,bool(self.type.currentText()),'Type'),(self.image_path,bool(self.current and self.current.template_image.exists()),'Image')]:
            widget.setProperty('error',not ok); widget.style().unpolish(widget); widget.style().polish(widget)
            if not ok:bad.append(label)
        if bad:MessageDialog.info(self,'Required fields','Please fill: '+', '.join(bad)); return False
        return True
    def _build_overlay(self,r:TemplateRecord):
        with Image.open(r.template_image) as source:
            base=source.convert('RGBA')
        b=r.artwork_box; ov=base.copy(); ov.paste(Image.new('RGBA',(b.width,b.height),(0,0,0,0)),(b.x,b.y)); path=r.folder/'overlay.png'; _atomic_save_png(ov,path); r.overlay_image=path
    def save_current(self):
        if not self.current or not self._validate():return
        if not MessageDialog.confirm(self,'Save template?','Save the current template image, boxes and properties?',primary='Save',secondary='Cancel'):return
        if self.pending_image_source and self.pending_image_source.exists():
            final_image=self.current.folder/'template.png'
            with Image.open(self.pending_image_source) as source:
                replacement=source.convert('RGBA')
            _atomic_save_png(replacement,final_image); self.current.template_image=final_image; self.pending_image_source.unlink(missing_ok=True); self.pending_image_source=None
        # Keep the asset card thumbnail synchronized with the saved production image.
        try:
            thumb_path = self.current.folder / 'thumbnail.png'
            with Image.open(self.current.template_image) as source:
                thumb = source.convert('RGBA')
            thumb.thumbnail((520, 300), Image.Resampling.LANCZOS)
            _atomic_save_png(thumb,thumb_path)
            self.current.thumbnail_image = thumb_path
        except Exception:
            pass
        self._build_overlay(self.current); save_record(self.current); self.saved_snapshot=deepcopy(self.current); self.dirty=False; self.unsaved.setText('Saved'); self.set_properties_enabled(False); self.reload(self.current.key); self.templates_changed.emit(); MessageDialog.info(self,'Template saved','Template changes were saved successfully.')
    def delete_records(self,keys):
        if not keys:return
        records=[self.records[k] for k in keys if k in self.records]
        protected=[r for r in records if r.locked or r.is_default]
        if protected:
            names='\n'.join(f'• {r.name}' for r in protected)
            MessageDialog.info(self,'Protected template',f'These templates are protected and cannot be deleted while locked/default:\n\n{names}\n\nYou can duplicate them. Custom locked templates can be unlocked first.'); return
        if not MessageDialog.confirm(self,'Delete templates?',f'Delete {len(records)} selected template(s)? This cannot be undone.',primary='Delete',secondary='Cancel',kind='danger'):return
        for r in records:delete_template(r)
        self.current=None; self.reload(); self.templates_changed.emit()
    def duplicate_records(self,keys):
        for k in keys:
            if k in self.records:duplicate_template(self.records[k])
        self.reload(); self.templates_changed.emit(); self.status.emit('Template duplicate(s) created.')
    def lock_records(self,keys,value):
        for k in keys:
            if k in self.records:set_locked(self.records[k],value)
        self.reload(); self.templates_changed.emit()
    def reveal_record(self,key):
        if key in self.records:reveal_in_file_manager(self.records[key].json_path)
    def move_records(self,keys,group):
        if str(group).lower() == 'defaults':
            self.status.emit('Defaults is permanent and does not accept custom templates.'); return
        groups={g['name']:g for g in load_groups()}; color=groups.get(group,{'color':'Blue'})['color']
        moved=0; blocked=0
        for k in keys:
            if k not in self.records: continue
            record=self.records[k]
            if record.is_default:
                blocked += 1; continue
            set_group(record,group,color); moved += 1
        self.reload(); self.templates_changed.emit()
        if blocked:
            self.status.emit(f'Moved {moved} template(s). {blocked} default template(s) stayed in Defaults.')
        else:
            self.status.emit(f'Moved {moved} template(s) to {group}.')
