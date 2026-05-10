import datetime

from kivy.clock import Clock
from kivy.uix.screenmanager import Screen
from kivy.properties import StringProperty, BooleanProperty

from app.theme import APP_VERSION
from app.services.settings_service import SettingsService


class SettingsScreen(Screen):
    app_version   = StringProperty(APP_VERSION)
    ntrip_enabled = BooleanProperty(False)
    marker_style  = StringProperty('snowcat')   # 'snowcat' | 'dot'

    def on_enter(self):
        data  = SettingsService.load()
        ntrip = data['ntrip']
        self.ntrip_enabled           = ntrip['enabled']
        self.ids.inp_host.text       = ntrip['host']
        self.ids.inp_port.text       = ntrip['port']
        self.ids.inp_mountpoint.text = ntrip['mountpoint']
        self.ids.inp_username.text   = ntrip['username']
        self.ids.inp_password.text   = ntrip['password']
        self.marker_style            = data['map']['marker_style']
        self._clock_event = Clock.schedule_interval(self._tick_clock, 1)
        self._tick_clock(0)

    def on_leave(self):
        if hasattr(self, '_clock_event'):
            self._clock_event.cancel()

    def _tick_clock(self, _dt):
        now = datetime.datetime.now()
        self.ids.lbl_time_set.text = now.strftime("%H:%M:%S")
        self.ids.lbl_date_set.text = now.strftime("%A, %d %b %Y")

    def toggle_ntrip(self):
        self.ntrip_enabled = not self.ntrip_enabled
        data = SettingsService.load()
        data['ntrip']['enabled'] = self.ntrip_enabled
        SettingsService.save()

    def toggle_marker_style(self):
        self.marker_style = 'dot' if self.marker_style == 'snowcat' else 'snowcat'
        SettingsService.load()['map']['marker_style'] = self.marker_style
        SettingsService.save()

    def save_ntrip(self):
        data = SettingsService.load()
        n = data['ntrip']
        n['enabled']    = self.ntrip_enabled
        n['host']       = self.ids.inp_host.text.strip()
        n['port']       = self.ids.inp_port.text.strip()
        n['mountpoint'] = self.ids.inp_mountpoint.text.strip()
        n['username']   = self.ids.inp_username.text.strip()
        n['password']   = self.ids.inp_password.text
        SettingsService.save()
        self.ids.btn_save_ntrip.text = 'Saved'
        Clock.schedule_once(lambda _dt: setattr(self.ids.btn_save_ntrip, 'text', 'Save'), 1.5)
