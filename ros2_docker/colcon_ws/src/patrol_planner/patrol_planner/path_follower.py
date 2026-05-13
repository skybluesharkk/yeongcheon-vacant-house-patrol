# -*- coding: utf-8 -*-
"""
경로 추종(path following) 로직

ROS와 분리된 순수 파이썬 클래스로 작성한다. 입력은 (현재 위치, 현재 yaw)이고
출력은 (linear, angular) 속도 명령이다. patrol_node에서 이 클래스를 보유하고
Twist 메시지로 변환해 /cmd_vel에 발행한다.

동작 정책 (간단한 2단계 컨트롤러):
    1) 현재 yaw와 목표 방향의 차이가 yaw_tolerance보다 크면
       제자리 회전(angular만 출력, linear=0)으로 자세부터 맞춘다.
    2) yaw가 충분히 정렬되면 전진(linear>0)하며 미세한 angular로 보정.
    3) 다음 웨이포인트까지의 거리가 arrival_tolerance 이내면 그 점에 도착한 것으로
       판정하고 다음 웨이포인트로 인덱스를 진행한다.
"""

import math


def normalize_angle(angle):
    """[-pi, pi] 범위로 정규화."""
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


class PathFollower:
    def __init__(self,
                 linear_speed=3.0,
                 angular_speed=1.0,
                 yaw_tolerance=0.3,
                 arrival_tolerance=3.0):
        # 속도/오차 파라미터
        self.linear_speed = linear_speed
        self.angular_speed = angular_speed
        self.yaw_tolerance = yaw_tolerance
        self.arrival_tolerance = arrival_tolerance

        # 따라갈 웨이포인트 좌표 리스트와 현재 인덱스
        self._waypoints = []
        self._idx = 0

    # ------------------------------------------------------------------
    # 경로 세팅/상태 조회
    # ------------------------------------------------------------------
    def set_path(self, waypoints):
        """추종할 (x, y) 좌표 시퀀스 설정."""
        self._waypoints = list(waypoints)
        self._idx = 0

    def is_finished(self):
        """모든 웨이포인트를 통과했는지."""
        return self._idx >= len(self._waypoints)

    def current_target(self):
        """현재 추종 중인 목표점. 끝났으면 None."""
        if self.is_finished():
            return None
        return self._waypoints[self._idx]

    def current_index(self):
        return self._idx

    def advance(self):
        """다음 웨이포인트로 강제 진행 (도착 처리 등에서 호출)."""
        self._idx += 1

    # ------------------------------------------------------------------
    # 컨트롤 계산
    # ------------------------------------------------------------------
    def compute_cmd(self, cur_x, cur_y, cur_yaw):
        """
        현재 위치/자세를 받아서 다음 (linear, angular) 명령 계산.

        Returns:
            dict: {
                "linear": float,
                "angular": float,
                "arrived": bool,        # 이번 호출에서 현재 목표점에 도착했는지
                "distance": float,      # 목표까지 거리
                "yaw_error": float,     # 목표 방향과의 yaw 오차 (rad)
                "target": (x, y) | None,
                "done": bool,           # 모든 웨이포인트 종료 여부
            }
        """
        if self.is_finished():
            return {
                "linear": 0.0,
                "angular": 0.0,
                "arrived": False,
                "distance": 0.0,
                "yaw_error": 0.0,
                "target": None,
                "done": True,
            }

        # 현재 목표 좌표와 거리.
        tx, ty = self._waypoints[self._idx]
        dx = tx - cur_x
        dy = ty - cur_y
        distance = math.hypot(dx, dy)

        # 도착 판정.
        # 도착했어도 cmd_vel=(0,0) 한 틱을 내보내는 대신, 인덱스를 진행시키고
        # 같은 틱 내에서 곧장 다음 웨이포인트 기준으로 제어 명령을 계산해
        # 차량이 끊김 없이 이어 달리도록 한다.
        arrived = False
        arrived_target = None
        if distance <= self.arrival_tolerance:
            arrived = True
            arrived_target = (tx, ty)
            self._idx += 1
            if self.is_finished():
                return {
                    "linear": 0.0,
                    "angular": 0.0,
                    "arrived": True,
                    "distance": 0.0,
                    "yaw_error": 0.0,
                    "target": arrived_target,
                    "done": True,
                }
            # 다음 웨이포인트로 갱신하고 계속 진행
            tx, ty = self._waypoints[self._idx]
            dx = tx - cur_x
            dy = ty - cur_y
            distance = math.hypot(dx, dy)

        target_yaw = math.atan2(dy, dx)
        yaw_error = normalize_angle(target_yaw - cur_yaw)

        # yaw 오차가 크면 회전 우선
        if abs(yaw_error) > self.yaw_tolerance:
            # 비례 제어로 회전: 가까워질수록 자연스럽게 감속해 오버슈트 방지.
            # 너무 느려지지 않도록 하한도 둔다.
            sign = 1.0 if yaw_error > 0 else -1.0
            mag = abs(yaw_error) * 1.5
            mag = min(self.angular_speed, max(0.2, mag))
            angular = sign * mag
            linear = 0.0
        else:
            # 전진하면서 미세 보정.
            # 주의: 전진과 큰 각속도를 동시에 주면 회전반경 = linear/angular 의 원호로 돌게 된다.
            # 따라서 전진 모드에서는 angular_speed 의 일부만 쓰도록 클램프한다 (회전반경 충분히 크게).
            linear = self.linear_speed
            angular = 2.0 * yaw_error  # 작은 P-gain
            max_ang_forward = self.angular_speed * 0.3
            if angular > max_ang_forward:
                angular = max_ang_forward
            elif angular < -max_ang_forward:
                angular = -max_ang_forward

        return {
            "linear": linear,
            "angular": angular,
            # arrived 는 위에서 도착했는지 여부.
            # arrived_target 은 도착한 직전 웨이포인트, target 은 지금부터 갈 다음 웨이포인트.
            "arrived": arrived,
            "arrived_target": arrived_target,
            "distance": distance,
            "yaw_error": yaw_error,
            "target": (tx, ty),
            "done": False,
        }
