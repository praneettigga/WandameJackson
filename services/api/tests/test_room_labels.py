import importlib.util
import shutil
import sys

import cv2
import numpy as np
import pytest

from roomshift_api import auto_scale
from roomshift_api.auto_scale import Label
from roomshift_api.parser import parse_blueprint
from roomshift_api.room_labels import assign_room_names, read_room_names, room_name
from conftest import l_shaped_plan


def room(x=0):
    return {'name': 'Room 1', 'polygon': [[x, 0], [x + 4, 0], [x + 4, 4], [x, 4]],
            'provenance': {'fieldOrigins': {'name': 'inferred'}, 'notes': []}}


@pytest.mark.parametrize('text', ['BEDROOM 2', 'Master Bedroom', 'LIVING / DINING', 'W.C.', 'Kitchen', 'Pooja Room'])
def test_room_vocabulary(text):
    assert room_name(text) == text


@pytest.mark.parametrize('text', ['4500 mm', '12 x 10', 'FIRST FLOOR PLAN', 'BEDROOM 12 m2', 'SCALE 1:100', 'SOFA'])
def test_notes_and_dimensions_are_not_room_names(text):
    assert room_name(text) is None


def test_spatial_assignment_full_phrases_and_fallback():
    rooms = [room(), room(4), room(8)]
    assign_room_names(rooms, [Label('Bedroom', 120, 100, 70, 20, 99),
                              Label('Master Bedroom', 50, 100, 140, 20, 90),
                              Label('Kitchen', 500, 100, 100, 20, 95),
                              Label('Office', 850, 100, 100, 20, 40),
                              Label('Garage', 1400, 100, 100, 20, 99)], .01)
    assert [r['name'] for r in rooms] == ['Master Bedroom', 'Kitchen', 'Room 1']
    assert rooms[0]['provenance']['fieldOrigins']['name'] == 'evidence'
    assert rooms[2]['provenance']['fieldOrigins']['name'] == 'inferred'


def test_ocr_scaling_and_parser_integration(monkeypatch):
    def read(image):
        sy, sx = image.shape[0] / 620, image.shape[1] / 640
        return [Label('Living Room', 140 * sx, 190 * sy, 100 * sx, 20 * sy, 99)]
    monkeypatch.setattr(auto_scale, 'read_labels', read)
    result = parse_blueprint(l_shaped_plan(), .025)
    assert any(r['name'] == 'Living Room' for r in result['rooms'])
    assert any(r['name'].startswith('Room ') for r in result['rooms'])


def test_deskew_uses_original_coordinates_for_room_names(monkeypatch):
    image = l_shaped_plan()
    h, w = image.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), 4, 1)
    rotated = cv2.warpAffine(image, matrix, (w, h), borderValue=255)
    cx, cy = matrix @ np.array([180, 220, 1])
    def read(work):
        sy, sx = work.shape[0] / h, work.shape[1] / w
        return [Label('Living Room', (cx - 40) * sx, (cy - 10) * sy, 80 * sx, 20 * sy, 99)]
    monkeypatch.setattr(auto_scale, 'read_labels', read)
    result = parse_blueprint(rotated, .025)
    assert any('rotated' in warning for warning in result['warnings'])
    assert any(r['name'] == 'Living Room' for r in result['rooms'])


@pytest.mark.skipif(not (shutil.which('tesseract') or (sys.platform == 'darwin' and importlib.util.find_spec('Vision'))), reason='OCR engine not installed')
def test_real_ocr_reads_a_room_name():
    image = np.full((500, 600), 255, np.uint8)
    cv2.rectangle(image, (80, 80), (520, 420), 0, 10)
    cv2.putText(image, 'KITCHEN', (180, 250), cv2.FONT_HERSHEY_SIMPLEX, 1, 0, 2)
    result = parse_blueprint(image, .01)
    assert [r['name'].upper() for r in result['rooms']] == ['KITCHEN']


def test_ocr_failure_preserves_geometry_and_manual_fallback(monkeypatch):
    def unavailable(_):
        raise RuntimeError('Unavailable')
    monkeypatch.setattr(auto_scale, 'read_labels', unavailable)
    result = {'rooms': [room()], 'warnings': []}
    read_room_names(np.full((300, 300), 255, np.uint8), result, .01)
    assert result['rooms'][0]['name'] == 'Room 1'
    assert 'Properties' in result['warnings'][0]
