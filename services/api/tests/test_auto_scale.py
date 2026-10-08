import importlib.util
import shutil
import sys

import cv2
import numpy as np
import pytest

from roomshift_api import auto_scale
from roomshift_api.auto_scale import Label, _dimension_span, estimate_scale, parse_measurement
from conftest import l_shaped_plan, png_bytes, wait_job


@pytest.mark.parametrize('text,unit,meters', [
    ('4500 mm', None, 4.5), ('450 cm', None, 4.5), ('4.50 m', None, 4.5),
    ("12'-6\"", None, 3.81), ('12′ 6″', None, 3.81), ('12 ft 6 in', None, 3.81),
    ('12\' 6 1/2"', None, 3.8227), ('36"', None, .9144), ('4,500 mm', None, 4.5),
    ('4500', 'mm', 4.5), ('4500', None, None), ('1:100', None, None),
    ('20 m2', None, None), ('BEDROOM 12', None, None), ('4,5 m', None, None),
    ('0 m', None, None), ('12\' 15"', None, None),
])
def test_measurement_units(text, unit, meters):
    actual = parse_measurement(text, unit)
    assert actual is None if meters is None else actual == pytest.approx(meters)


def dimensioned_plan(text='5.00 m'):
    image = np.full((600, 800), 255, np.uint8)
    cv2.rectangle(image, (150, 200), (650, 500), 0, 10)
    cv2.line(image, (150, 120), (650, 120), 0, 2)
    for x in (150, 650):
        cv2.line(image, (x, 100), (x, 190), 0, 2)
    (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 1, 2)
    x, y = 400 - w // 2, 100
    cv2.putText(image, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1, 0, 2)
    return image, Label(text, x, y - h, w, h, 99)


def stub_labels(monkeypatch, label, original_shape=(600, 800)):
    calls = []
    def read(gray):
        calls.append(1)
        if len(calls) != 1:
            return []
        sy, sx = gray.shape[0] / original_shape[0], gray.shape[1] / original_shape[1]
        return [Label(label.text, label.x * sx, label.y * sy, label.w * sx, label.h * sy, label.confidence)]
    monkeypatch.setattr(auto_scale, 'read_labels', read)


def test_dimension_geometry_and_original_pixel_scale(monkeypatch):
    image, label = dimensioned_plan()
    span = _dimension_span(255 - image, label)
    assert span is not None
    assert span[1][0] - span[0][0] == pytest.approx(500, abs=2)
    stub_labels(monkeypatch, label)
    cal = estimate_scale(image)
    assert cal['method'] == 'printed-dimension'
    assert cal['metersPerPixel'] == pytest.approx(.01, rel=.01)
    assert cal['pointA'][0] == pytest.approx(150, abs=2)


def test_number_without_an_associated_dimension_span_is_not_a_scale(monkeypatch):
    image, label = dimensioned_plan()
    image[90:195, 145:155] = 255
    image[90:195, 645:655] = 255
    image[118:123, :] = 255
    # An underline that does not align with the outside walls is not evidence.
    cv2.line(image, (250, 120), (550, 120), 0, 2)
    stub_labels(monkeypatch, label)
    assert estimate_scale(image)['method'] == 'wall-thickness'


def test_conflicting_measurements_fall_back(monkeypatch):
    image, label = dimensioned_plan()
    cv2.line(image, (150, 560), (650, 560), 0, 2)
    for x in (150, 650):
        cv2.line(image, (x, 530), (x, 585), 0, 2)
    labels = [label, Label('9.00 m', label.x, 518, label.w, label.h, 99)]
    calls = []
    def read(gray):
        calls.append(1)
        return [Label(p.text, p.x * 2, p.y * 2, p.w * 2, p.h * 2, 99) for p in labels] if len(calls) == 1 else []
    monkeypatch.setattr(auto_scale, 'read_labels', read)
    cal = estimate_scale(image)
    assert cal['method'] == 'wall-thickness'
    assert any('disagree' in note for note in cal['notes'])


def test_no_ocr_still_assigns_explicit_estimate(monkeypatch):
    def unavailable(_):
        raise RuntimeError('OCR unavailable')
    monkeypatch.setattr(auto_scale, 'read_labels', unavailable)
    cal = estimate_scale(l_shaped_plan())
    assert cal['method'] == 'wall-thickness'
    assert cal['metersPerPixel'] == pytest.approx(.025)
    assert 'OCR unavailable' in cal['notes']
    empty = estimate_scale(np.full((100, 100), 255, np.uint8))
    assert empty['method'] == 'image-extent'


def test_api_automatic_default_and_manual_override(client, monkeypatch):
    image, label = dimensioned_plan()
    stub_labels(monkeypatch, label)
    upload = client.post('/api/projects', files={'blueprint': ('plan.png', png_bytes(image), 'image/png')}).json()
    pid = upload['project']['id']
    detected = client.get(f'/api/projects/{pid}/scale').json()['calibration']
    assert detected['method'] == 'printed-dimension'
    assert client.get(f'/api/projects/{pid}/scale').json()['calibration'] == detected
    response = client.post(f'/api/projects/{pid}/reconstruct', json={})
    assert response.status_code == 202
    assert wait_job(client, response.json()['job']['id'])['status'] == 'succeeded'
    scene = client.get(f'/api/projects/{pid}/scene').json()
    assert scene['source']['calibration'] == detected
    assert any('printed dimension' in note for note in scene['reconstruction']['warnings'])
    manual = {'pointA': [150, 200], 'pointB': [650, 200], 'distanceMeters': 10}
    response = client.post(f'/api/projects/{pid}/reconstruct', json={'calibration': manual})
    assert wait_job(client, response.json()['job']['id'])['status'] == 'succeeded'
    assert client.get(f'/api/projects/{pid}/scene').json()['source']['calibration']['metersPerPixel'] == .02


def test_api_estimates_without_preview(client, monkeypatch):
    monkeypatch.setattr(auto_scale, 'read_labels', lambda _: [])
    pid = client.post('/api/projects', files={'blueprint': ('plan.png', png_bytes(l_shaped_plan()), 'image/png')}).json()['project']['id']
    response = client.post(f'/api/projects/{pid}/reconstruct', json={})
    assert wait_job(client, response.json()['job']['id'])['status'] == 'succeeded'
    scene = client.get(f'/api/projects/{pid}/scene').json()
    assert scene['source']['calibration']['method'] == 'wall-thickness'
    assert scene['walls'][0]['provenance']['fieldOrigins']['start'] == 'inferred'


@pytest.mark.skipif(not (shutil.which('tesseract') or (sys.platform == 'darwin' and importlib.util.find_spec('Vision'))), reason='OCR engine not installed')
@pytest.mark.parametrize('rotation', [0, 1, 3])
def test_real_ocr_dimensioned_image(rotation):
    image, _ = dimensioned_plan()
    image = np.ascontiguousarray(np.rot90(image, rotation))
    cal = estimate_scale(image)
    assert cal['method'] == 'printed-dimension', cal
    assert cal['metersPerPixel'] == pytest.approx(.01, rel=.02)


def test_dimension_line_interrupted_by_text_and_chained_segments():
    ink = np.zeros((250, 650), np.uint8)
    cv2.line(ink, (50, 120), (600, 120), 255, 2)
    for x in (50, 300, 600):
        cv2.line(ink, (x, 100), (x, 150), 255, 2)
    ink[116:125, 125:225] = 0
    span = _dimension_span(ink, Label('2.5 m', 130, 105, 90, 25, 99))
    assert span is not None
    assert span[0][0] == pytest.approx(50, abs=2)
    assert span[1][0] == pytest.approx(300, abs=2)


def test_explicit_unit_note_and_low_confidence_rejection(monkeypatch):
    image, label = dimensioned_plan('5000')
    def read(gray):
        if gray.shape != (1200, 1600):
            return []
        return [Label(label.text, label.x * 2, label.y * 2, label.w * 2, label.h * 2, 99),
                Label('ALL DIMENSIONS IN mm', 20, 1100, 400, 20, 99)]
    monkeypatch.setattr(auto_scale, 'read_labels', read)
    assert estimate_scale(image)['metersPerPixel'] == pytest.approx(.01, rel=.01)
    stub_labels(monkeypatch, Label('5 m', label.x, label.y, label.w, label.h, 30))
    assert estimate_scale(image)['method'] == 'wall-thickness'


@pytest.mark.skipif(not (shutil.which('tesseract') or (sys.platform == 'darwin' and importlib.util.find_spec('Vision'))), reason='OCR engine not installed')
@pytest.mark.parametrize('text,meters', [('5000 mm', 5), ("16'-5\"", 5.0038)])
def test_real_ocr_units(text, meters):
    image, _ = dimensioned_plan(text)
    cal = estimate_scale(image)
    assert cal['method'] == 'printed-dimension', cal
    assert cal['metersPerPixel'] == pytest.approx(meters / 500, rel=.02)


def test_overall_dimension_without_ticks_matches_outer_walls(monkeypatch):
    # Same notation as the uploaded apartment: dimension above the outside walls.
    image = np.full((700, 1000), 255, np.uint8)
    cv2.rectangle(image, (100, 100), (900, 600), 0, 14)
    cv2.line(image, (100, 80), (900, 80), 0, 2)
    label = Label('8.0 m', 470, 51, 60, 20, 99)
    stub_labels(monkeypatch, label, image.shape)
    cal = estimate_scale(image)
    assert cal['method'] == 'printed-dimension'
    assert cal['distanceMeters'] == 8
    assert cal['metersPerPixel'] == pytest.approx(.01, rel=.01)
    # An arbitrary short underline above the drawing must not set scale.
    image[78:83, :] = 255
    cv2.line(image, (350, 80), (650, 80), 0, 2)
    stub_labels(monkeypatch, label, image.shape)
    assert estimate_scale(image)['method'] == 'wall-thickness'
