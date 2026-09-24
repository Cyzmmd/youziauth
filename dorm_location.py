"""Acquire real Windows location; reject stale/low-accuracy positions.

Windows produces WGS84. The mobile form uses offset map coordinates (GCJ02).
Conversion is approximate; the school verify endpoint remains authoritative.

A replayed sample is reported like a fresh fix: same provider, same fields, and a small bounded
drift, because identical coordinates repeated night after night are easy to spot in history.
"""
import asyncio
import datetime as dt
import json
import math
import os
import random
import re
import time
from pathlib import Path

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
    'sample_missing': '本机尚未保存模拟定位样本，请先准备样本或切换为真实定位。',
    'sample_invalid': '模拟定位样本无法读取或数据无效，请检查本地样本或切换为真实定位。',
}


ACCURACY_LIMIT_M = 200.0

# A point chosen on the map is not a measurement, so it states a plausible Wi-Fi-range accuracy
# instead of pretending to be a precise fix. The replay then drifts it like any other sample.
PICK_SOURCE = 'MAP_PICK'
PICK_ACCURACY_M = 100.0

# School-facing label for every accepted position. A replayed sample must not be distinguishable
# from a live Windows fix, so both sources report this same value inside mapData / qddz; the active
# source is tracked locally in Settings.location_source and never leaves the machine.
POSITION_PROVIDER = 'windows'

# A live fix never repeats itself exactly, so a replayed one must not either: identical numbers
# every night are a stronger fingerprint than any payload field. Drift stays well inside the
# school's own range check (the task radius is hundreds of metres), and the accuracy spread never
# pushes the value across the acceptance limit.
DRIFT_RATIO = 0.2        # drift radius as a fraction of the sample's stated accuracy
DRIFT_FLOOR_M = 3.0      # metres; keeps a precise sample from looking pinned
DRIFT_CAP_M = 25.0       # metres; never wander far enough to threaten the range check
ACCURACY_SPREAD = 0.15   # ±15 % on the reported accuracy

_RNG = random.Random()


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


def gcj02_to_wgs84(lat, lng):
    """Inverse of wgs84_to_gcj02, by bounded iteration (sub-metre after three passes).

    The school publishes its check-in point in GCJ02 while the map picker works in WGS84, so
    the reference has to be readable in the frame the user clicks in.
    """
    if not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271):
        return lat, lng
    guess_lat, guess_lng = lat, lng
    for _ in range(3):
        forward_lat, forward_lng = wgs84_to_gcj02(guess_lat, guess_lng)
        guess_lat += lat - forward_lat
        guess_lng += lng - forward_lng
    return guess_lat, guess_lng


def distance_metres(lat1, lng1, lat2, lng2):
    """Great-circle distance on a spherical Earth. The school's own check stays authoritative."""
    radius = 6371000.0
    first, second = math.radians(lat1), math.radians(lat2)
    delta_lat, delta_lng = second - first, math.radians(lng2 - lng1)
    a = (math.sin(delta_lat / 2) ** 2
         + math.cos(first) * math.cos(second) * math.sin(delta_lng / 2) ** 2)
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def radius_metres(value):
    """Read the school's radius text ('800米', '800') as metres; None when it is unusable."""
    match = re.search(r'\d+(?:\.\d+)?', str(value or ''))
    return float(match.group()) if match else None


def map_pick_sample(latitude, longitude, *, accuracy=PICK_ACCURACY_M, timestamp=None):
    """Turn a raw WGS84 map pick into a sample the replay path accepts.

    A pick is stored exactly like a captured Windows fix, so the replay stays the only place that
    converts to GCJ02 and drifts the point: the school receives the same shape of position either
    way, and only the local sample records how the point was chosen.
    """
    try:
        lat, lng, acc = float(latitude), float(longitude), float(accuracy)
    except (TypeError, ValueError):
        raise ValueError('选点坐标无效，请在地图上重新选择。') from None
    if (not all(math.isfinite(value) for value in (lat, lng, acc))
            or not -90 <= lat <= 90 or not -180 <= lng <= 180):
        raise ValueError('选点坐标无效，请在地图上重新选择。')
    if acc <= 0 or acc > ACCURACY_LIMIT_M:
        raise ValueError('选点精度超出可提交范围，请重新选择。')
    return {'latitude': lat, 'longitude': lng, 'accuracy': acc,
            'timestamp': time.time() if timestamp is None else float(timestamp),
            'source': PICK_SOURCE}


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
    if accuracy > ACCURACY_LIMIT_M:
        raise LocationFailure('inaccurate')
    if not -10 <= time.time()-stamp <= 120:
        raise LocationFailure('stale')
    lat, lng = wgs84_to_gcj02(lat, lng)
    return {'latitude': lat, 'longitude': lng, 'accuracy': accuracy, 'time': int(stamp*1000),
            'address': '', 'province': '', 'city': '', 'district': '', 'road': '',
            'provider': POSITION_PROVIDER, 'isOffset': True, 'errorCode': 0, 'errorMessage': ''}


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


def _drift_point(lat, lng, accuracy, rng):
    """Move a replayed fix the way a fresh one would differ, by a bounded random offset."""
    radius = min(DRIFT_CAP_M, max(DRIFT_FLOOR_M, accuracy * DRIFT_RATIO))
    distance = radius * math.sqrt(rng.random())          # uniform over the disc, not the edge
    bearing = rng.uniform(0, 2 * math.pi)
    metres_per_degree = 111_320.0
    north = distance * math.cos(bearing) / metres_per_degree
    east = distance * math.sin(bearing) / (metres_per_degree * max(0.01, math.cos(math.radians(lat))))
    return lat + north, lng + east


def _drift_accuracy(accuracy, rng):
    """Vary the reported accuracy without ever crossing the acceptance limit."""
    high = min(accuracy * (1 + ACCURACY_SPREAD), ACCURACY_LIMIT_M * 0.99)
    low = min(accuracy * (1 - ACCURACY_SPREAD), high)
    return rng.uniform(low, high)


def simulate_location(sample, *, latitude=None, longitude=None, accuracy=None, timestamp=None, rng=None):
    if not isinstance(sample, dict):
        raise ValueError('定位样本必须是 JSON 对象')
    if not math.isfinite(float(sample['timestamp'])) or not isinstance(sample.get('source'), str):
        raise ValueError('定位样本缺少有效采样信息')
    raw = dict(sample)
    for key, value in (('latitude', latitude), ('longitude', longitude), ('accuracy', accuracy)):
        if value is not None:
            raw[key] = value
    # Replay clock is simulated; it does not indicate a fresh Windows fix.
    raw['timestamp'] = time.time() if timestamp is None else timestamp
    # validate_position stamps the same provider a live fix carries, so the sample stays
    # indistinguishable in the submission; only the local source setting records the choice.
    position = validate_position(raw)
    # Drift only what the caller did not pin down, so an explicit --latitude/--longitude/--accuracy
    # request still reproduces one exact point. The school gates the drifted values like any other.
    jitter = rng or _RNG
    if latitude is None and longitude is None:
        position['latitude'], position['longitude'] = _drift_point(
            position['latitude'], position['longitude'], position['accuracy'], jitter)
    if accuracy is None:
        position['accuracy'] = _drift_accuracy(position['accuracy'], jitter)
    return position


def locate(source='windows', sample_path=None):
    if source == 'windows':
        return validate_position(read_position())
    if source != 'simulation':
        raise CheckinError('location_required', '定位来源无效，请重新保存设置。')
    if sample_path is None:
        raise LocationFailure('sample_missing')
    try:
        sample = json.loads(Path(sample_path).read_text(encoding='utf-8'))
        return simulate_location(sample)
    except FileNotFoundError:
        raise LocationFailure('sample_missing') from None
    except (LocationFailure, OSError, ValueError, TypeError, KeyError):
        raise LocationFailure('sample_invalid') from None


def probe_location(ui_dispatch=None, *, source='windows', sample_path=None, label=''):
    """Local quality check only. Never return coordinates or call the school API.

    `label` names the simulation point being replayed, so the check says which saved position it
    just used instead of leaving the user to guess which one is active.
    """
    try:
        if source == 'windows' and ui_dispatch is not None:
            permission = request_permission(ui_dispatch)
            if permission != 'ALLOWED':
                raise LocationFailure('denied' if permission == 'DENIED' else 'unspecified')
        position = locate(source, sample_path)
        accuracy = round(position['accuracy'], 1)
        named = f'当前使用选点「{label}」，' if source == 'simulation' and label else ''
        message = (f'模拟定位检测通过：{named}采样精度约 {accuracy:g} 米。'
                   '基于已保存位置并随机偏移，并非当前位置；未提交打卡。'
                   if source == 'simulation' else
                   f'定位检测通过，精度约 {accuracy:g} 米。未提交打卡；正式提交时会重新获取位置。')
        return {'state':'ready', 'message':message,
                'accuracy':accuracy, 'checked':dt.datetime.now().strftime('%H:%M:%S')}
    except LocationFailure as exc:
        return {'state':exc.code, 'message':str(exc), 'accuracy':None,
                'checked':dt.datetime.now().strftime('%H:%M:%S')}
    except CheckinError as exc:
        return {'state':'error', 'message':str(exc), 'accuracy':None, 'checked':''}
