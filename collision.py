from typing import Tuple

CollisionResult = Tuple[bool, str]


def boxes_intersect(a_min_x: float, a_max_x: float, a_min_y: float, a_max_y: float,
                    b_min_x: float, b_max_x: float, b_min_y: float, b_max_y: float) -> bool:
    return not (a_max_x < b_min_x or a_min_x > b_max_x or a_max_y < b_min_y or a_min_y > b_max_y)


def check_collision(existing: Tuple[float, float, float, float],
                    new_part: Tuple[float, float, float, float]) -> CollisionResult:
    existing_min_x, existing_max_x, existing_min_y, existing_max_y = existing
    new_min_x, new_max_x, new_min_y, new_max_y = new_part
    if boxes_intersect(existing_min_x, existing_max_x, existing_min_y, existing_max_y,
                       new_min_x, new_max_x, new_min_y, new_max_y):
        return True, "Bounding boxes overlap"
    return False, "No collision"
