# -*- coding: utf-8 -*-
"""
경로 계획 모듈

- a_star: 두 노드 사이의 최단 경로 (도로 그래프 위에서)
- tsp_order: 여러 빈집을 도는 방문 순서 결정 (Nearest Neighbor heuristic)
- compute_full_path: 시작 좌표 + 빈집 ID 리스트 -> (x, y) 좌표 시퀀스
"""

import heapq
import math

from .graph import NODES, EDGES, HOUSES, HOUSE_NODES, nearest_node


# ---------------------------------------------------------------------------
# 기본 유틸
# ---------------------------------------------------------------------------
def euclidean(a, b):
    """두 (x, y) 점 사이의 유클리디안 거리."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ---------------------------------------------------------------------------
# A* 알고리즘
# ---------------------------------------------------------------------------
def a_star(start_node, goal_node, nodes=NODES, edges=EDGES):
    """
    도로 그래프 위에서 start_node -> goal_node 최단 경로 탐색.

    Args:
        start_node (str): 시작 노드 id
        goal_node (str): 목표 노드 id
        nodes (dict): {node_id: (x, y)}
        edges (dict): {node_id: [인접 node_id 리스트]}

    Returns:
        list[str]: 시작 노드부터 목표 노드까지의 노드 id 리스트.
                   경로가 없으면 빈 리스트.
    """
    if start_node not in nodes or goal_node not in nodes:
        return []
    if start_node == goal_node:
        return [start_node]

    # heapq 항목: (f, g, node, parent)
    # f = g + h (h: 목표까지 유클리디안 휴리스틱)
    open_heap = []
    heapq.heappush(open_heap, (0.0, 0.0, start_node, None))

    came_from = {}      # node -> parent node
    g_score = {start_node: 0.0}
    closed = set()

    while open_heap:
        f, g, current, parent = heapq.heappop(open_heap)

        if current in closed:
            continue
        closed.add(current)
        came_from[current] = parent

        # 목표 도달 -> 경로 역추적
        if current == goal_node:
            path = []
            cur = current
            while cur is not None:
                path.append(cur)
                cur = came_from[cur]
            path.reverse()
            return path

        # 이웃 확장
        for nb in edges.get(current, []):
            if nb in closed:
                continue
            step_cost = euclidean(nodes[current], nodes[nb])
            tentative_g = g + step_cost
            if tentative_g < g_score.get(nb, float("inf")):
                g_score[nb] = tentative_g
                h = euclidean(nodes[nb], nodes[goal_node])
                heapq.heappush(open_heap, (tentative_g + h, tentative_g, nb, current))

    # 경로 없음
    return []


def path_length(path, nodes=NODES):
    """노드 id 리스트로 표현된 경로의 총 길이."""
    if not path or len(path) < 2:
        return 0.0
    total = 0.0
    for i in range(len(path) - 1):
        total += euclidean(nodes[path[i]], nodes[path[i + 1]])
    return total


# ---------------------------------------------------------------------------
# TSP (Nearest Neighbor heuristic)
# ---------------------------------------------------------------------------
def tsp_order(start_node, house_ids, nodes=NODES, edges=EDGES,
              house_nodes=HOUSE_NODES):
    """
    시작 노드에서 출발해 모든 빈집을 한 번씩 방문하는 순서를 결정.

    완전한 최적해(TSP) 대신 nearest neighbor heuristic을 사용한다.
    빈집 수가 적을 때(<=10) 충분히 합리적인 순서를 만들어주며,
    영찬님이 추후 더 좋은 휴리스틱(2-opt 등)으로 교체 가능.

    Args:
        start_node (str): 출발 노드 id
        house_ids (list[str]): 방문할 빈집 id 리스트
        nodes (dict)
        edges (dict)
        house_nodes (dict): 빈집 -> 가장 가까운 도로 노드 매핑

    Returns:
        list[str]: 방문 순서대로 정렬된 빈집 id 리스트
    """
    remaining = [h for h in house_ids if h in house_nodes]
    order = []
    current_node = start_node

    while remaining:
        best_house = None
        best_dist = float("inf")
        for h in remaining:
            target_node = house_nodes[h]
            path = a_star(current_node, target_node, nodes, edges)
            if not path:
                # 도달 불가 -> 일단 큰 값으로 처리
                continue
            d = path_length(path, nodes)
            if d < best_dist:
                best_dist = d
                best_house = h

        if best_house is None:
            # 남은 빈집들 모두 도달 불가 -> 그대로 종료
            break

        order.append(best_house)
        current_node = house_nodes[best_house]
        remaining.remove(best_house)

    return order


# ---------------------------------------------------------------------------
# 전체 경로 계산
# ---------------------------------------------------------------------------
def compute_full_path(start_pos, house_ids, return_to_start=True,
                      nodes=NODES, edges=EDGES, houses=HOUSES,
                      house_nodes=HOUSE_NODES):
    """
    시작 좌표에서 출발해 모든 빈집을 순회하고 (옵션) 시작점으로 복귀하는
    전체 경로를 (x, y) 좌표 리스트로 반환.

    각 빈집 구간은 다음 순서로 만들어진다:
        1) 현재 노드 → 빈집의 nearest_node 까지 A* 경로 (도로 노드들)
        2) nearest_node 다음에 빈집의 실제 위치 HOUSES[h]["pos"] 를 마지막 웨이포인트로 추가
    arrival_indices 는 (2)번 단계의 인덱스를 가리킨다 → 도착 처리는 빈집 실제 위치에서.

    Args:
        start_pos (tuple[float, float]): 차량의 현재 (x, y) (odom)
        house_ids (list[str]): 방문할 빈집 id 리스트
        return_to_start (bool): True면 마지막에 시작 노드로 복귀
        nodes, edges, houses, house_nodes: 그래프 데이터

    Returns:
        dict: {
            "waypoints": [(x, y), ...],          # 차량이 따라갈 좌표 시퀀스
            "house_order": [house_id, ...],      # 결정된 방문 순서
            "arrival_indices": [i1, i2, ...],    # 각 빈집 도착 웨이포인트 인덱스
            "arrival_yaws": [yaw1, yaw2, ...],   # 각 빈집 도착 시 회전할 yaw (rad)
        }
    """
    start_node = nearest_node(start_pos[0], start_pos[1], nodes)
    order = tsp_order(start_node, house_ids, nodes, edges, house_nodes)

    waypoints = [tuple(start_pos)]  # 시작 위치 그대로 첫 점으로
    arrival_indices = []
    arrival_yaws = []

    current_node = start_node
    for h in order:
        target_node = house_nodes[h]
        seg = a_star(current_node, target_node, nodes, edges)
        if not seg:
            # 경로 없음 -> 스킵
            continue
        # seg[0]은 current_node 이므로 중복 방지 위해 1부터
        for nid in seg[1:]:
            waypoints.append(nodes[nid])
        # 도로 노드 도착 후, 빈집 실제 좌표를 마지막 웨이포인트로 한 번 더 찍어
        # 차량이 도로 → 빈집 쪽으로 약간 더 접근하도록 한다.
        house_pos = tuple(houses[h]["pos"])
        waypoints.append(house_pos)
        arrival_indices.append(len(waypoints) - 1)
        arrival_yaws.append(float(houses[h].get("yaw", 0.0)))
        current_node = target_node

    if return_to_start:
        seg = a_star(current_node, start_node, nodes, edges)
        if seg:
            for nid in seg[1:]:
                waypoints.append(nodes[nid])

    return {
        "waypoints": waypoints,
        "house_order": order,
        "arrival_indices": arrival_indices,
        "arrival_yaws": arrival_yaws,
    }
