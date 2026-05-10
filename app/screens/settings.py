import datetime

from kivy.clock import Clock
from kivy.uix.screenmanager import Screen
from kivy.properties import StringProperty, BooleanProperty

from app.theme import APP_VERSION
from app.services.data_service import DataService
from app.services.settings_service import SettingsService


class SettingsScreen(Screen):
    app_version   = StringProperty(APP_VERSION)
    ntrip_enabled = BooleanProperty(False)
    marker_style  = StringProperty('snowcat')   # 'snowcat' | 'dot'
    imu_heading_enabled = BooleanProperty(False)
    imu_heading_status = StringProperty('IMU disconnected')
    can_calibrate_imu = BooleanProperty(False)

    def on_enter(self):
        data  = SettingsService.load()
        ntrip = data['ntrip']
        map_settings = data['map']
        self.ntrip_enabled           = ntrip['enabled']
        self.ids.inp_host.text       = ntrip['host']
        self.ids.inp_port.text       = ntrip['port']
        self.ids.inp_mountpoint.text = ntrip['mountpoint']
        self.ids.inp_username.text   = ntrip['username']
        self.ids.inp_password.text   = ntrip['password']
        self.marker_style            = map_settings['marker_style']
        self.imu_heading_enabled     = map_settings['imu_heading_enabled']
        self._bind_imu_status()
        self._clock_event = Clock.schedule_interval(self._tick_clock, 1)
        self._tick_clock(0)

    def on_leave(self):
        if hasattr(self, '_clock_event'):
            self._clock_event.cancel()
        self._unbind_imu_status()

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

    def toggle_imu_heading_control(self):
        self.imu_heading_enabled = not self.imu_heading_enabled
        data = SettingsService.load()
        data['map']['imu_heading_enabled'] = self.imu_heading_enabled
        SettingsService.save()

    def calibrate_imu_heading(self):
        ds = DataService.get()
        success = ds.calibrate_imu_heading_to_boot()
        self._on_imu_status()
        if success:
            self.ids.btn_calibrate_imu.text = 'Calibrated'
        else:
            self.ids.btn_calibrate_imu.text = 'No Yaw'
        Clock.schedule_once(
            lambda _dt: setattr(self.ids.btn_calibrate_imu, 'text', 'Calibrate'),
            1.5,
        )

    def _bind_imu_status(self):
        if getattr(self, '_imu_status_bound', False):
            self._on_imu_status()
            return

        ds = DataService.get()
        ds.refresh_imu_heading_calibration()
        ds.bind(
            imu_ok=self._on_imu_status,
            imu_yaw_valid=self._on_imu_status,
            imu_yaw_deg=self._on_imu_status,
            imu_heading_deg=self._on_imu_status,
            imu_heading_calibrated=self._on_imu_status,
        )
        self._imu_status_bound = True
        self._on_imu_status()

    def _unbind_imu_status(self):
        if not getattr(self, '_imu_status_bound', False):
            return

        ds = DataService.get()
        ds.unbind(
            imu_ok=self._on_imu_status,
            imu_yaw_valid=self._on_imu_status,
            imu_yaw_deg=self._on_imu_status,
            imu_heading_deg=self._on_imu_status,
            imu_heading_calibrated=self._on_imu_status,
        )
        self._imu_status_bound = False

    def _on_imu_status(self, *_args):
        ds = DataService.get()
        self.can_calibrate_imu = ds.imu_ok and ds.imu_yaw_valid
        if not ds.imu_ok:
            self.imu_heading_status = 'IMU disconnected'
        elif not ds.imu_yaw_valid:
            self.imu_heading_status = 'Waiting for yaw data'
        elif not ds.imu_heading_calibrated:
            self.imu_heading_status = f'Raw yaw {ds.imu_yaw_deg:.1f} deg; not calibrated'
        else:
            self.imu_heading_status = (
                f'Heading {ds.imu_heading_deg:.1f} deg; raw {ds.imu_yaw_deg:.1f} deg'
            )

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
