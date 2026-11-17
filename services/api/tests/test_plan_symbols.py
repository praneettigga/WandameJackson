"""Door kinds, corridor crossings and the repository's harder fixture plans."""
import math
from collections import Counter

import cv2
from shapely.geometry import LineString, Point, Polygon
import numpy as np
import pytest

from eval.generate import generate
from eval.metrics import evaluate, symbol_metrics
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


def stairs(result):
    return [o for o in result['objects'] if o['category'] == 'stairs']


@pytest.mark.parametrize('name,centre,size', [
    ('complex-01-house-with-stairs.png', (4.8, 8.6), (1.2, 2.4)),
    ('complex-02-l-plan-stair-hall.png', (8.5, 8.4), (2.0, 2.0)),
    ('complex-03-apartment-stair-core.png', (12.8, 3.75), (1.6, 2.5)),
])
def test_straight_stair_is_read_with_its_size_and_arrow(name, centre, size):
    stair, = stairs(parsed(name, 0.02))
    assert stair['componentId'] == 'stairs.straight'
    assert stair['position'][0] == pytest.approx(centre[0], abs=0.2) and stair['position'][2] == pytest.approx(centre[1], abs=0.35)
    assert stair['dimensions'][0] == pytest.approx(size[0], abs=0.1) and stair['dimensions'][2] == pytest.approx(size[1], abs=0.45)
    # The arrow points up the page, which is the local climb direction: no turn. The label under it is not the arrow head.
    assert stair['rotationY'] == 0 and 'arrow' in stair['provenance']['notes'][2]
    assert stair['dimensions'][1] == 2.7 and stair['provenance']['fieldOrigins']['dimensions'] == 'inferred'


def test_two_flights_side_by_side_are_one_u_stair_and_mark_the_stairwell():
    for name in ('floor-1.png', 'floor-4.png'):
        result = parsed(name, 0.01)
        stair, = stairs(result)
        # Up the left flight to the landing at the top wall, back down the page on the right one.
        assert stair['componentId'] == 'stairs.u' and stair['rotationY'] == 0
        assert stair['position'][0] == pytest.approx(7.0, abs=0.1) and stair['dimensions'][0] == pytest.approx(1.6, abs=0.1)
        well = next(r for r in result['rooms'] if r['name'] == 'STAIRS')
        assert well['type'] == 'stair'
    assert not stairs(parsed('floor-1.png', 0.01, stairs=False))


def test_generated_stairs_are_found_with_their_direction_and_nothing_else_is_a_stair():
    found = wanted = right = judged = 0
    for seed in range(20):
        plan = generate(seed, symbols=True)
        result = parse_blueprint(plan.image, plan.mpp)
        metrics = symbol_metrics(result, plan.meta['objects'])
        wanted += len(plan.meta['objects'])
        found += len(stairs(result))
        if not math.isnan(metrics['stairs_dir_acc']):
            judged += 1
            right += metrics['stairs_dir_acc']
        # A plan without a staircase (too little room for one) has none found: no score either way.
        if plan.meta['objects']:
            assert metrics['stairs_precision'] == 1 and metrics['stairs_recall'] == 1
        else:
            assert not stairs(result) and math.isnan(metrics['stairs_f1'])
    assert wanted >= 8 and found == wanted and right == judged
    # Plans without stairs, with windows, dimension lines and room labels, have none.
    assert not any(stairs(parse_blueprint(generate(seed).image, 0.02)) for seed in range(10))
    for name, mpp in (('apartment_blueprint.png', 0.01), ('services-apartment.png', 0.02), ('synthetic-06-four-rooms.png', 0.0073)):
        assert not stairs(parsed(name, mpp))


def test_curved_bay_wall_is_fitted_as_an_arc_and_closes_its_rooms():
    from shapely.geometry import Point, Polygon
    layout = rooms_found = 0.0
    for seed in range(8):
        plan = generate(seed, curved=True)
        result = parse_blueprint(plan.image, plan.mpp)
        metrics = evaluate(result, {'walls': plan.walls, 'openings': plan.openings, 'rooms': plan.rooms})
        layout += metrics['layout_iou']
        rooms_found += metrics['room_f1']
        pieces = [w for w in result['walls'] if 'curved wall' in w['provenance']['notes'][0]]
        assert len(pieces) >= 4 and any('curved wall(s)' in w for w in result['warnings'])
        # The pieces follow the drawn bay: every piece's middle is within a wall thickness of the true arc.
        truth = Polygon([p for room in plan.rooms for p in room]).convex_hull.exterior
        for w in pieces:
            middle = ((w['start'][0] + w['end'][0]) / 2, (w['start'][1] + w['end'][1]) / 2)
            assert truth.distance(Point(middle)) < 0.25
    assert layout / 8 > 0.95 and rooms_found / 8 > 0.95
    # Without arc fitting the bay leaves rooms open.
    plan = generate(6, curved=True)
    off = parse_blueprint(plan.image, plan.mpp, options=ParserOptions(curved_walls=False))
    assert evaluate(off, {'walls': plan.walls, 'openings': plan.openings, 'rooms': plan.rooms})['layout_iou'] < 0.5


def test_straight_plans_get_no_arcs():
    for seed in range(6):
        for options in ({}, {'chamfer': True}, {'symbols': True}):
            plan = generate(seed, **options)
            result = parse_blueprint(plan.image, plan.mpp)
            assert not any('curved wall' in w['provenance']['notes'][0] for w in result['walls'])
    for name, mpp in (('floor-1.png', 0.01), ('services-apartment.png', 0.02), ('complex-01-house-with-stairs.png', 0.02)):
        assert not any('curved wall' in w['provenance']['notes'][0] for w in parsed(name, mpp)['walls'])


def test_furniture_and_fixture_symbols_become_objects_facing_away_from_their_wall():
    from collections import Counter
    found = drawn = wrong = 0
    kinds = Counter()
    typed = rooms = 0
    for seed in range(12):
        plan = generate(seed, furnished=True)
        result = parse_blueprint(plan.image, plan.mpp)
        metrics = symbol_metrics(result, plan.meta['objects'], plan.meta['room_types'])
        truth = [o for o in plan.meta['objects'] if o['category'] != 'stairs']
        objects = [o for o in result['objects'] if o['category'] != 'stairs']
        drawn += len(truth)
        found += round(metrics['fixture_recall'] * len(truth)) if truth else 0
        wrong += len(objects) - (round(metrics['fixture_precision'] * len(objects)) if objects else 0)
        kinds.update(o['name'].split()[0].lower() for o in objects)
        typed += metrics['room_type_acc'] * len(plan.meta['room_types'])
        rooms += len(plan.meta['room_types'])
        for o in objects:
            assert o['provenance']['fieldOrigins'] == {'dimensions': 'inferred'} and 'Plan symbol read as' in o['provenance']['notes'][0]
    assert found >= 0.85 * drawn and wrong == 0
    assert set(kinds) == {'bed', 'sofa', 'table', 'cabinet', 'toilet', 'sink', 'bathtub', 'shower', 'stove'}
    assert typed >= 0.6 * rooms  # rooms take a use from a toilet, bath, shower, cooktop, bed or sofa


def test_wall_backed_symbol_turns_its_back_to_the_wall():
    import math
    plan = generate(1, furnished=True)
    result = parse_blueprint(plan.image, plan.mpp)
    for o in result['objects']:
        if o['componentId'] not in ('bed.basic', 'sofa.basic', 'cabinet.basic'):
            continue
        # The back (local -z) points at the nearest wall: a point 0.1 m behind the object is beside a wall.
        back = (o['position'][0] - math.sin(o['rotationY']) * (o['dimensions'][2] / 2 + 0.1),
                o['position'][2] - math.cos(o['rotationY']) * (o['dimensions'][2] / 2 + 0.1))
        near = min(Point(back).distance(LineString([w['start'], w['end']])) for w in result['walls'])
        assert near < 0.25, o['name']


def test_plans_without_symbols_get_no_furniture():
    for seed in range(6):
        for options in ({}, {'symbols': True}, {'curved': True}):
            plan = generate(seed, **options)
            assert not [o for o in parse_blueprint(plan.image, plan.mpp)['objects'] if o['category'] != 'stairs']
    for name, mpp in (('floor-1.png', 0.01), ('services-apartment.png', 0.02), ('apartment_blueprint.png', 0.01)):
        assert not [o for o in parsed(name, mpp)['objects'] if o['category'] != 'stairs']


def prior_from(plan, extra_room=None):
    """A perfect stand-in for the learned model: walls where the plan's walls are, rooms inside them."""
    h, w = plan.image.shape
    rooms = np.zeros((h, w), np.uint8)
    for room in plan.rooms:
        cv2.fillPoly(rooms, [np.round(np.asarray(room) / plan.mpp).astype(np.int32)], 4)
    for wall in plan.walls:
        a, b = (tuple(int(round(v / plan.mpp)) for v in wall[k]) for k in ('start', 'end'))
        cv2.line(rooms, a, b, 2, max(2, int(round(wall['thickness'] / plan.mpp))))
    if extra_room is not None:
        cv2.rectangle(rooms, *extra_room, 11, -1)
    return {'rooms': rooms, 'icons': np.zeros_like(rooms), 'wall': (rooms == 2).astype(np.uint8) * 255}


def test_learned_prior_traces_walls_rooms_and_records_the_model():
    plan = generate(3)
    result = parse_blueprint(plan.image, plan.mpp, prior=prior_from(plan), wall_finder='learned')
    metrics = evaluate(result, {'walls': plan.walls, 'openings': plan.openings, 'rooms': plan.rooms})
    assert result['model']['license'] == 'CC-BY-NC-4.0'
    assert metrics['layout_iou'] > 0.9 and metrics['room_f1'] >= 0.8
    assert any('learned plan model' in w for w in result['warnings'])


def test_auto_keeps_the_rules_on_a_clean_plan_and_rules_ignores_the_prior():
    plan = generate(3)
    prior = prior_from(plan)
    auto = parse_blueprint(plan.image, plan.mpp, prior=prior, wall_finder='auto')
    assert auto.get('model') is None and any("drawing's own walls" in w for w in auto['warnings'])
    assert parse_blueprint(plan.image, plan.mpp, prior=prior, wall_finder='rules').get('model') is None
    # A prior of the wrong size is ignored rather than misread.
    small = {k: v[:10, :10] for k, v in prior.items()}
    assert parse_blueprint(plan.image, plan.mpp, prior=small, wall_finder='learned').get('model') is None


def test_room_cover_tells_clean_results_from_open_ones():
    from roomshift_api.parser import _room_cover
    plan = generate(3)
    full = parse_blueprint(plan.image, plan.mpp)
    assert _room_cover(full) > 0.9
    assert _room_cover({**full, 'rooms': full['rooms'][:1]}) < _room_cover(full)
    assert _room_cover({**full, 'rooms': []}) == 0


def test_learned_rooms_outside_the_building_are_dropped():
    plan = generate(3)
    h, w = plan.image.shape
    outside = ((w - 30, h - 30), (w - 2, h - 2))  # a labelled patch in the sheet margin
    result = parse_blueprint(plan.image, plan.mpp, prior=prior_from(plan, outside), wall_finder='learned')
    corner = Point((w - 16) * plan.mpp, (h - 16) * plan.mpp)
    assert not any(Polygon(r['polygon']).contains(corner) for r in result['rooms'])
