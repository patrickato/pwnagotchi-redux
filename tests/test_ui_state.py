"""Shared production presentation contract; no framebuffer/input/network I/O."""
import json
from copy import deepcopy

import pytest

from redux.display import fbui
from redux.display.ui_state import PAGES, freshness, model, navigation
from redux.display.gesture_nav import TAB_TOP, TAB_WIDTH, NavigationGestures, consume_gestures
from test_gesture_nav import calibration, contact


def report():
    return {'runtime': {'updated_utc': 100, 'visual_sampled_utc': 99,
                        'state': 'running', 'free_bytes': 1048576,
                        'capture_file': '/captures/prospective.pcap'},
            'telemetry': {'measured_at': 98, 'age_seconds': 2, 'fresh': True,
                          'values': {'cpu_temp_c': 45, 'cpu_load_pct': 0, 'ram_pct': 25,
                                     'cpu_frequency_mhz': 1500, 'throttled_mask': 0},
                          'provenance': {'cpu_temp_c': 'sysfs:cpu-thermal'}},
            'doctor': {'overall': 'unknown', 'coverage': {'not_assessed': ['power']},
                       'findings': [{'status': 'unknown', 'summary': 'Power unavailable',
                                     'remediation': 'Connect a supported sensor'}]},
            'governor': {'mode': 'full', 'measurement_fresh': True, 'reason': 'observed thermal'}}


def row(view, name):
    return next(item for item in view['rows'] if item['label'] == name)


def test_real_measurements_and_independent_freshness():
    data = report()
    original = deepcopy(data)
    view = model(data, now=101)
    assert row(view, 'CPU temperature') == {'label': 'CPU temperature', 'value': '45.0 C',
        'state': 'fresh', 'source': 'sysfs:cpu-thermal', 'measured_at': 98}
    assert row(view, 'CPU utilization')['value'] == '0.0%'
    assert row(view, 'Battery')['state'] == row(view, 'Power source')['state'] == 'unknown'
    assert row(view, 'Firmware flags')['value'] == '0x0'
    assert row(view, 'Capture path')['value'] == 'Configured / existence unverified'
    assert 'Connect a supported sensor' in row(view, 'Doctor unknown')['source']
    assert row(view, 'Coverage unknown')['value'] == 'power'
    assert data == original
    data['runtime']['updated_utc'] = 120
    view = model(data, now=120)
    assert view['heartbeat']['state'] == 'fresh'
    assert view['sensors']['state'] == view['visual']['state'] == 'stale'
    assert row(view, 'Governor')['state'] == 'unknown'
    assert row(view, 'CPU temperature')['value'] == '45.0 C'  # visibly historical, never fabricated


@pytest.mark.parametrize('bad', [None, [], True, 'bad', float('nan'), float('inf'), -200, pytest.param(10**5000, id='oversized')])
def test_malformed_numeric_values_cannot_be_live(bad):
    data = report()
    data['telemetry']['measured_at'] = bad
    data['telemetry']['values'] = dict.fromkeys(data['telemetry']['values'], bad)
    view = model(data, now=101)
    assert view['sensors']['state'] != 'fresh'
    assert row(view, 'CPU temperature')['value'] == 'Unavailable'
    assert row(view, 'Firmware flags')['value'] == 'Unavailable'
    assert row(view, 'Governor')['state'] == 'unknown'
    json.dumps(view, allow_nan=False)


@pytest.mark.parametrize('bad', [None, [], 'bad', False, 5])
@pytest.mark.parametrize('producer', ['telemetry', 'runtime', 'doctor', 'governor', 'capture_processing', 'radio_inventory'])
def test_independent_producers_fail_without_crashing(bad, producer):
    data = report()
    data[producer] = bad
    view = model(data, now=101)
    assert view['version'] == 1
    assert len(view['rows']) <= 80
    assert fbui.render(data, page='system', now=101).size == (480, 320)


@pytest.mark.parametrize('stamp,state', [(None, 'unknown'), (True, 'unknown'), (float('nan'), 'unknown'),
                                       (80, 'stale'), (102, 'stale'), (100, 'fresh')])
def test_freshness_requires_real_past_timestamp(stamp, state):
    assert freshness(stamp, 101, 10)['state'] == state


def test_navigation_has_no_page_count_limit_and_real_touch_targets():
    pages = tuple('page' + str(n) for n in range(40))
    for page in pages:
        visible = navigation(page, pages)
        assert page in visible and len(visible) == 5
    assert 320 - TAB_TOP >= 44 and TAB_WIDTH >= 44
    for page in PAGES:
        tracker = NavigationGestures(calibration(), physically_verified=True, page=page)
        for index, selected in enumerate(navigation(page)):
            actions, tail = consume_gestures(contact((index * TAB_WIDTH + 30, 300)), tracker)
            assert actions == [('page', selected)] and tail == b''


def test_all_pages_repeat_without_leaks_or_unbounded_models():
    data = report()
    data['doctor']['findings'] *= 200
    data['doctor']['coverage']['not_assessed'] *= 200
    for _ in range(20):
        assert len(model(data, now=101)['rows']) <= 80
    for page in PAGES:
        image = fbui.render(data, page=page, now=101)
        assert image.size == (480, 320)
        assert image.mode == 'RGB'
    fbui.render(data, page='system', system_offset=10**6, now=101)
    assert fbui._font.cache_info().currsize <= 24


def test_api_adds_canonical_view_without_mutating_runtime_or_doctor():
    import threading
    import time
    import urllib.request
    from http.server import ThreadingHTTPServer
    from redux.web.status_page import make_handler
    data = report()
    now = time.time()
    data['runtime']['updated_utc'] = now
    data['telemetry']['measured_at'] = now
    original = deepcopy(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(lambda: data))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/status', timeout=3) as response:
            result = json.load(response)
        assert result['runtime'] == original['runtime']
        assert result['doctor'] == original['doctor']
        assert result['ui']['sensors']['state'] == 'fresh'
        assert row(result['ui'], 'CPU temperature')['value'] == '45.0 C'
        assert data == original
        assert fbui.render(result, page='system', now=now).size == (480, 320)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_preview_exposes_same_system_scroll_as_live_renderer():
    from redux.display import preview
    from PIL import Image
    from io import BytesIO
    image = preview.build(report=report(), pages=('system',), system_offset=6)['system']
    assert Image.open(BytesIO(image)).size == (480, 320)
    assert image != preview.build(report=report(), pages=('system',))['system']
    with pytest.raises(ValueError):
        preview.build(system_offset=78)


@pytest.mark.parametrize('width', [80, 166, 266, 444])
@pytest.mark.parametrize('size', [9, 11, 12, 20])
def test_native_glyphs_fit_measured_columns(width, size):
    from PIL import Image, ImageDraw
    draw = ImageDraw.Draw(Image.new('RGB', (480, 320)))
    font = fbui._font(size)
    fitted = fbui.fit_text(draw, 'W' * 160, font, width)
    assert fitted.endswith('...')
    assert draw.textlength(fitted, font=font) <= width


def test_firmware_flags_never_substitute_for_measured_power():
    data = report()
    data['telemetry']['values']['throttled_mask'] = 5
    view = model(data, now=101)
    assert row(view, 'Firmware current')['value'] == 'undervoltage, throttled'
    assert row(view, 'Power source')['state'] == 'unknown'
    assert row(view, 'Battery')['value'] == 'Unavailable'


def test_system_row_tap_opens_read_only_detail_and_renders_full_evidence():
    tracker = NavigationGestures(calibration(), physically_verified=True, page='system')
    assert consume_gestures(contact((250, 170)), tracker)[0] == [('detail', 0)]
    assert consume_gestures(contact((250, 235)), tracker)[0] == [('detail', 2)]
    data = report()
    first = fbui.render(data, page='system', now=101)
    detail = fbui.render(data, page='system', system_detail=0, now=101)
    assert detail.tobytes() != first.tobytes()
    assert detail.size == first.size


def test_full_governor_without_any_measurement_stays_unknown():
    data = report()
    data['telemetry']['values'] = {}
    assert row(model(data, now=101), 'Governor')['state'] == 'unknown'
