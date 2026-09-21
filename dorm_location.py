"""Acquire real Windows location; reject stale/low-accuracy positions.

Windows produces WGS84. The mobile form uses offset map coordinates (GCJ02).
Conversion is approximate; the school verify endpoint remains authoritative.
"""
import asyncio
import datetime as dt
import math
import os
import time

from dorm_checkin import CheckinError


MESSAGES = {
    'denied': 'Windows 拒绝了此应用的定位访问。请在定位设置中允许位置服务和桌面应用访问，然后重试。',
    'unspecified': 'Windows 尚未完成定位授权，请保持主窗口在前台，再点击“授权并检测定位”。',
    'disabled': 'Windows 定位服务已关闭或被系统策略禁用，请检查系统定位设置。',
    'no_data': '定位权限可用，但 Windows 暂未取得位置信号。请打开 Wi-Fi，保持网络可用，稍后重新检测。',
    'timeout': '等待实时定位超时。请打开 Wi-Fi 并保持网络可用，稍后重试；开启权限不代表一定能取得定位。',
    'not_available': '此设备暂时没有可用的定位提供程序，请检查 Windows 位置服务与无线网卡。',
    'inaccurate': '已取得位置，但精度未达到 200 米要求。请打开 Wi-Fi，稍后重新检测；本次不会提交打卡。',
    'stale': 'Windows 返回的位置已过期，请稍后重新检测；本次不会提交打卡。',
    'default_position': 'Windows 返回的是默认位置或 IP 粗略位置，不能用于打卡。请打开 Wi-Fi 获取实时定位。',
    'invalid': 'Windows 返回的定位数据无效，未提交；请重新检测。',
    'component_missing': '定位组件缺失，请安装完整新版程序后重试。',
    'error': 'Windows 定位接口调用失败，请重新打开程序后检测；这不一定是权限未开启。',
}


class LocationFailure(CheckinError):
    def __init__(self, code):
        self.code = code
        super().__init__('location_required', MESSAGES[code])


def wgs84_to_gcj02(lat, lng):
    if not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271):
        return lat, lng
    x, y = lng - 105, lat - 35
    a = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*math.sqrt(abs(x))
    b = 300 + x + 2*y + .1*x*x + .1*x*y + .1*math.sqrt(abs(x))
    wave = (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
    a += wave + (20*math.sin(y*math.pi)+40*math.sin(y*math.pi/3))*2/3
    a += (160*math.sin(y*math.pi/12)+320*math.sin(y*math.pi/30))*2/3
    b += wave + (20*math.sin(x*math.pi)+40*math.sin(x*math.pi/3))*2/3
    b += (150*math.sin(x*math.pi/12)+300*math.sin(x*math.pi/30))*2/3
    rad = math.radians(lat)
    magic = 1 - .00669342162296594323 * math.sin(rad)**2
    root = math.sqrt(magic)
    a = a * 180 / ((6378245 * (1-.00669342162296594323))/(magic*root)*math.pi)
    b = b * 180 / (6378245/root*math.cos(rad)*math.pi)
    return lat+a, lng+b


def validate_position(data):
    try:
        lat, lng, accuracy, stamp = (float(data[k]) for k in ('latitude', 'longitude', 'accuracy', 'timestamp'))
        if (not all(math.isfinite(v) for v in (lat, lng, accuracy, stamp)) or
                not -90 <= lat <= 90 or not -180 <= lng <= 180 or accuracy <= 0):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise LocationFailure('invalid') from None
    if data.get('source') in ('DEFAULT', 'IP_ADDRESS', 'OBFUSCATED'):
        raise LocationFailure('default_position')
    if accuracy > 200:
        raise LocationFailure('inaccurate')
    if not -10 <= time.time()-stamp <= 120:
        raise LocationFailure('stale')
    lat, lng = wgs84_to_gcj02(lat, lng)
    return {'latitude': lat, 'longitude': lng, 'accuracy': accuracy, 'time': int(stamp*1000),
            'address': '', 'province': '', 'city': '', 'district': '', 'road': '',
            'provider': 'windows', 'isOffset': True, 'errorCode': 0, 'errorMessage': ''}


def _winrt_modules():
    if os.name != 'nt':
        raise CheckinError('location_required', '自动定位仅支持 Windows')
    try:
        import winrt.runtime as runtime
        import winrt.windows.foundation  # Registers async-operation projections.
        import winrt.windows.devices.geolocation as geo
        return runtime, geo
    except ImportError:
        raise LocationFailure('component_missing') from None


def _failure(exc, status=''):
    code = getattr(exc, 'winerror', None) or getattr(exc, 'hresult', None) or 0
    if code & 0xffffffff == 0x80070005:
        return LocationFailure('denied')
    if status == 'DISABLED':
        return LocationFailure('disabled')
    if status == 'NOT_AVAILABLE':
        return LocationFailure('not_available')
    if status == 'NO_DATA':
        return LocationFailure('no_data')
    if isinstance(exc, TimeoutError) or code & 0xffffffff in (0x800705B4, 0x80070102):
        return LocationFailure('timeout')
    return LocationFailure('error')


async def _wait(operation, timeout):
    return await asyncio.wait_for(operation, timeout)


def request_permission(ui_dispatch):
    """Create RequestAccessAsync on the actual foreground app's UI thread, never a helper process."""
    runtime, geo = _winrt_modules()
    runtime.init_apartment(runtime.ApartmentType.MULTI_THREADED)
    try:
        operation = ui_dispatch(geo.Geolocator.request_access_async)
        access = asyncio.run(_wait(operation, 60))
        return access.name
    except LocationFailure:
        raise
    except Exception as exc:
        raise _failure(exc) from None
    finally:
        runtime.uninit_apartment()


def read_position():
    """Wait for fresh data, rather than reading immediately after provider startup."""
    runtime, geo = _winrt_modules()
    runtime.init_apartment(runtime.ApartmentType.MULTI_THREADED)
    locator = None
    try:
        locator = geo.Geolocator()
        locator.desired_accuracy = geo.PositionAccuracy.HIGH
        operation = locator.get_geoposition_async_with_age_and_timeout(
            dt.timedelta(seconds=30), dt.timedelta(seconds=30))
        position = asyncio.run(_wait(operation, 35))
        c = position.coordinate
        return {'latitude': c.latitude, 'longitude': c.longitude,
                'accuracy': c.accuracy, 'timestamp': c.timestamp.timestamp(),
                'source': c.position_source.name}
    except Exception as exc:
        status = locator.location_status.name if locator is not None else ''
        raise _failure(exc, status) from None
    finally:
        runtime.uninit_apartment()


def locate():
    return validate_position(read_position())


def probe_location(ui_dispatch=None):
    """Local quality check only. Never return coordinates or call the school API."""
    try:
        if ui_dispatch is not None:
            permission = request_permission(ui_dispatch)
            if permission != 'ALLOWED':
                raise LocationFailure('denied' if permission == 'DENIED' else 'unspecified')
        position = validate_position(read_position())
        accuracy = round(position['accuracy'], 1)
        return {'state':'ready', 'message':f'定位检测通过，精度约 {accuracy:g} 米。未提交打卡；正式提交时会重新获取位置。',
                'accuracy':accuracy, 'checked':dt.datetime.now().strftime('%H:%M:%S')}
    except LocationFailure as exc:
        return {'state':exc.code, 'message':str(exc), 'accuracy':None,
                'checked':dt.datetime.now().strftime('%H:%M:%S')}
    except CheckinError as exc:
        return {'state':'error', 'message':str(exc), 'accuracy':None, 'checked':''}
