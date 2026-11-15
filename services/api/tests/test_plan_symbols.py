"""Door kinds, corridor crossings and the repository's harder fixture plans."""
import math
from collections import Counter

import cv2
import pytest

from eval.generate import generate
from eval.metrics import evaluate
from roomshift_api.config import REPO_ROOT
from roomshift_api.parser import ParserOptions, parse_blueprint

FIXTURES = REPO_ROOT / 'contracts/fixtures'


def parsed(name, mpp, **options):
    gray = cv2.imread(str(FIXTURES / name), cv2.IMREAD_GRAYSCALE)
    return parse_blueprint(gray, mpp, options=ParserOptions(**options))


def doors(result):
    return [o for o in result['openings'] if o['type'] == 'door']


def test_every_drawn_kind_of_door_is_read_as_that_kind():
    styled = right = found = drawn = 0
    kinds = Counter()
    for seed in range(30):
        plan = generate(seed, symbols=True)
        result = parse_blueprint(plan.image, plan.mpp)
        metrics = evaluate(result, {'walls': plan.walls, 'openings': plan.openings, 'rooms': plan.rooms})
        found, drawn = found + metrics['door_tp'], drawn + metrics['door_gt']
        if not math.isnan(metrics['door_style_acc']):
            styled += 1
            right += metrics['door_style_acc']
        kinds.update(o.get('style') for o in doors(result))
    assert found >= 0.9 * drawn and right / styled >= 0.95
    assert set(kinds) == {'hinged', 'double', 'sliding', 'folding', 'passage'}


def test_door_with_a_threshold_line_is_a_door_not_a_window():
    """These doors are drawn with their sill across the gap, which alone looks like glazing."""
    result = parsed('synthetic-05-three-rooms.png', 0.0072)
    assert len(doors(result)) == 3 and {o['style'] for o in doors(result)} == {'hinged'}
    assert sum(o['type'] == 'window' for o in result['openings']) == 5
    # Without symbol reading they are all taken for windows.
    assert not doors(parsed('synthetic-05-three-rooms.png', 0.0072, door_styles=False))


def test_two_doors_either_side_of_a_partition_end_are_two_doors():
    result = parsed('synthetic-06-four-rooms.png', 0.0073)
    assert len(result['rooms']) == 4 and [o['style'] for o in doors(result)] == ['hinged'] * 4


def test_corridor_crossing_a_wall_line_is_not_a_door():
    """The hall of this plan is one cross-shaped room; where it crosses a wall line there is no door."""
    result = parsed('floor-1.png', 0.01)
    assert len(result['rooms']) == 10 and len(doors(result)) == 10
    names = sorted(r['name'].upper() for r in result['rooms'])
    assert names == ['BATH', 'BEDROOM 1', 'BEDROOM 2', 'DINING', 'HALL', 'KITCHEN', 'LIFT', 'LIVING', 'STAIRS', 'UTILITY']
    types = {r['name'].upper(): r.get('type') for r in result['rooms']}
    assert types['STAIRS'] == 'stair' and types['LIFT'] == 'lift' and types['HALL'] == 'hall'
    hall = next(r for r in result['rooms'] if r['name'] == 'HALL')
    xs, zs = zip(*hall['polygon'])
    assert max(xs) - min(xs) == pytest.approx(12, abs=0.3) and max(zs) - min(zs) == pytest.approx(4, abs=0.3)


def test_door_hung_at_the_end_of_a_wall_closes_its_room():
    result = parsed('floor-4.png', 0.01)
    names = {r['name'].upper() for r in result['rooms']}
    assert len(result['rooms']) == 11 and {'STUDY', 'BATH', 'KITCHEN', 'MASTER BEDROOM'} <= names


def test_plain_gap_is_an_open_passage_and_old_scenes_stay_valid():
    result = parsed('room.png', 0.02)
    door, = doors(result)
    assert door['style'] == 'passage'
    legacy = parsed('room.png', 0.02, door_styles=False)
    assert 'style' not in doors(legacy)[0]
