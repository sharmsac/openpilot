import pyray as rl
from dataclasses import dataclass
from openpilot.common.constants import CV
from openpilot.selfdrive.ui.mici.onroad.torque_bar import TorqueBar
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget
from openpilot.common.filter_simple import FirstOrderFilter
from cereal import log

EventName = log.OnroadEvent.EventName

# Constants
SET_SPEED_NA = 255
KM_TO_MILE = 0.621371
CRUISE_DISABLED_CHAR = '–'
SET_SPEED_PERSISTENCE = 2.5  # seconds


@dataclass(frozen=True)
class FontSizes:
    current_speed: int = 176
    speed_unit: int = 66
    max_speed: int = 36
    set_speed: int = 112


@dataclass(frozen=True)
class Colors:
    WHITE = rl.WHITE
    WHITE_TRANSLUCENT = rl.Color(255, 255, 255, 200)


FONT_SIZES = FontSizes()
COLORS = Colors()


class TurnIntent(Widget):
    FADE_IN_ANGLE = 30  # degrees

    def __init__(self):
        super().__init__()
        self._pre = False
        self._turn_intent_direction: int = 0

        self._turn_intent_alpha_filter = FirstOrderFilter(0, 0.05, 1 / gui_app.target_fps)
        self._turn_intent_rotation_filter = FirstOrderFilter(0, 0.1, 1 / gui_app.target_fps)

        self._txt_turn_intent_left: rl.Texture = gui_app.texture('icons_mici/turn_intent_left.png', 50, 20)
        self._txt_turn_intent_right: rl.Texture = gui_app.texture('icons_mici/turn_intent_right.png', 50, 20)

    def _render(self, _):
        if self._turn_intent_alpha_filter.x > 1e-2:
            turn_intent_texture = self._txt_turn_intent_right if self._turn_intent_direction == 1 else self._txt_turn_intent_left
            src_rect = rl.Rectangle(0, 0, turn_intent_texture.width, turn_intent_texture.height)
            dest_rect = rl.Rectangle(self._rect.x + self._rect.width / 2, self._rect.y + self._rect.height / 2,
                                     turn_intent_texture.width, turn_intent_texture.height)
            origin = (turn_intent_texture.width / 2, self._rect.height / 2)
            color = rl.Color(255, 255, 255, int(255 * self._turn_intent_alpha_filter.x))
            rl.draw_texture_pro(turn_intent_texture, src_rect, dest_rect, origin, self._turn_intent_rotation_filter.x, color)

    def _update_state(self) -> None:
        sm = ui_state.sm

        left = any(e.name == EventName.preLaneChangeLeft for e in sm['onroadEvents'])
        right = any(e.name == EventName.preLaneChangeRight for e in sm['onroadEvents'])
        if left or right:
            if not self._pre:
                self._turn_intent_rotation_filter.x = self.FADE_IN_ANGLE if left else -self.FADE_IN_ANGLE
            self._pre = True
            self._turn_intent_direction = -1 if left else 1
            self._turn_intent_alpha_filter.update(1)
            self._turn_intent_rotation_filter.update(0)
        elif any(e.name == EventName.laneChange for e in sm['onroadEvents']):
            self._pre = False
            self._turn_intent_alpha_filter.update(0)
            if self._turn_intent_direction == 0:
                self._turn_intent_rotation_filter.update(0)
            else:
                self._turn_intent_rotation_filter.update(self._turn_intent_direction * self.FADE_IN_ANGLE)
        else:
            self._pre = False
            self._turn_intent_direction = 0
            self._turn_intent_alpha_filter.update(0)
            self._turn_intent_rotation_filter.update(0)


class HudRenderer(Widget):
    def __init__(self):
        super().__init__()
        """Initialize the HUD renderer."""
        self.is_cruise_set: bool = False
        self.is_cruise_available: bool = True
        self.set_speed: float = SET_SPEED_NA
        self._set_speed_changed_time: float = 0
        self.speed: float = 0.0
        self.v_ego_cluster_seen: bool = False
        self._engaged: bool = False

        self._can_draw_top_icons = True
        self._show_wheel_critical = False

        self._font_bold: rl.Font = gui_app.font(FontWeight.BOLD)
        self._font_medium: rl.Font = gui_app.font(FontWeight.MEDIUM)
        self._font_semi_bold: rl.Font = gui_app.font(FontWeight.SEMI_BOLD)
        self._font_display: rl.Font = gui_app.font(FontWeight.DISPLAY)

        self._turn_intent = TurnIntent()
        self._torque_bar = TorqueBar()

        self._txt_wheel: rl.Texture = gui_app.texture('icons_mici/wheel.png', 50, 50)
        self._txt_wheel_critical: rl.Texture = gui_app.texture('icons_mici/wheel_critical.png', 50, 50)
        self._txt_exclamation_point: rl.Texture = gui_app.texture('icons_mici/exclamation_point.png', 44, 44)

        self._wheel_alpha_filter = FirstOrderFilter(0, 0.05, 1 / gui_app.target_fps)
        self._wheel_y_filter = FirstOrderFilter(0, 0.1, 1 / gui_app.target_fps)

        self._set_speed_alpha_filter = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)

    def set_wheel_critical_icon(self, critical: bool):
        self._show_wheel_critical = critical

    def set_can_draw_top_icons(self, can_draw_top_icons: bool):
        self._can_draw_top_icons = can_draw_top_icons

    def drawing_top_icons(self) -> bool:
        return bool(self._set_speed_alpha_filter.x > 1e-2)

    def _update_state(self) -> None:
        sm = ui_state.sm
        if sm.recv_frame["carState"] < ui_state.started_frame:
            self.is_cruise_set = False
            self.set_speed = SET_SPEED_NA
            self.speed = 0.0
            return

        controls_state = sm['controlsState']
        car_state = sm['carState']

        v_cruise_cluster = car_state.vCruiseCluster
        set_speed = (
            controls_state.vCruiseDEPRECATED if v_cruise_cluster == 0.0 else v_cruise_cluster
        )
        engaged = sm['selfdriveState'].enabled
        if (set_speed != self.set_speed and engaged) or (engaged and not self._engaged):
            self._set_speed_changed_time = rl.get_time()
        self._engaged = engaged
        self.set_speed = set_speed
        self.is_cruise_set = 0 < self.set_speed < SET_SPEED_NA
        self.is_cruise_available = self.set_speed != -1

        v_ego_cluster = car_state.vEgoCluster
        self.v_ego_cluster_seen = self.v_ego_cluster_seen or v_ego_cluster != 0.0
        v_ego = v_ego_cluster if self.v_ego_cluster_seen else car_state.vEgo
        speed_conversion = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
        self.speed = max(0.0, v_ego * speed_conversion)

    def _render(self, rect: rl.Rectangle) -> None:
        if ui_state.sm['controlsState'].lateralControlState.which() != 'angleState':
            self._torque_bar.render(rect)

        if self.is_cruise_set:
            self._draw_set_speed(rect)

        #self._draw_steering_wheel(rect)

        # Draw current speed in high-left corner
        self._draw_current_speed(rect)

    def _draw_steering_wheel(self, rect: rl.Rectangle) -> None:
        wheel_txt = self._txt_wheel_critical if self._show_wheel_critical else self._txt_wheel

        if self._show_wheel_critical:
            self._wheel_alpha_filter.update(255)
            self._wheel_y_filter.update(0)
        else:
            if ui_state.status == UIStatus.DISENGAGED:
                self._wheel_alpha_filter.update(0)
                self._wheel_y_filter.update(wheel_txt.height / 2)
            else:
                self._wheel_alpha_filter.update(255 * 0.9)
                self._wheel_y_filter.update(0)

        pos_x = int(rect.x + 21 + wheel_txt.width / 2)
        pos_y = int(rect.y + rect.height - 14 - wheel_txt.height / 2 + self._wheel_y_filter.x)
        rotation = -ui_state.sm['carState'].steeringAngleDeg

        turn_intent_margin = 25
        self._turn_intent.render(rl.Rectangle(
            pos_x - wheel_txt.width / 2 - turn_intent_margin,
            pos_y - wheel_txt.height / 2 - turn_intent_margin,
            wheel_txt.width + turn_intent_margin * 2,
            wheel_txt.height + turn_intent_margin * 2,
        ))

        src_rect = rl.Rectangle(0, 0, wheel_txt.width, wheel_txt.height)
        dest_rect = rl.Rectangle(pos_x, pos_y, wheel_txt.width, wheel_txt.height)
        origin = (wheel_txt.width / 2, wheel_txt.height / 2)

        color = rl.Color(255, 255, 255, int(self._wheel_alpha_filter.x))
        rl.draw_texture_pro(wheel_txt, src_rect, dest_rect, origin, rotation, color)

        if self._show_wheel_critical:
            EXCLAMATION_POINT_SPACING = 10
            exclamation_pos_x = pos_x - self._txt_exclamation_point.width / 2 + wheel_txt.width / 2 + EXCLAMATION_POINT_SPACING
            exclamation_pos_y = pos_y - self._txt_exclamation_point.height / 2
            rl.draw_texture(self._txt_exclamation_point, int(exclamation_pos_x), int(exclamation_pos_y), rl.WHITE)

    def _draw_set_speed(self, rect: rl.Rectangle) -> None:
        alpha = self._set_speed_alpha_filter.update(
            self._can_draw_top_icons and self._engaged
        )
        if alpha < 1e-2:
            return

        SCALE = .9 # 20% larger

        base_radius = 162 // 2
        circle_radius = int(base_radius * SCALE)

        MARGIN = 5

        x = rect.x + rect.width - circle_radius * 2 - MARGIN
        y = rect.y + MARGIN

        rl.draw_circle_gradient(int(x + circle_radius), int(y + circle_radius), circle_radius,
                        rl.Color(0, 0, 0, int(255 / 2 * alpha)), rl.BLANK)

        ORANGE = rl.Color(255, 165, 0, int(255 * 0.9 * alpha))

        set_speed_color = ORANGE
        max_color = ORANGE

        set_speed = self.set_speed
        if self.is_cruise_set and not ui_state.is_metric:
            set_speed *= KM_TO_MILE

        set_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(set_speed))
        rl.draw_text_ex(
            self._font_display,
            set_speed_text,
            rl.Vector2(x + 13 + 4, y + 3 - 8 - 3 + 4),
            int(FONT_SIZES.set_speed * 0.55),  # ↓ smaller number
            0,
            set_speed_color,
        )

        # MAX LABEL (3x)
        rl.draw_text_ex(
            self._font_semi_bold,
            tr("MAX"),
            rl.Vector2(
                x + circle_radius * 0.42,
                y + circle_radius * 0.65,
        ),
        int(FONT_SIZES.max_speed * SCALE),
        0,
        max_color,
    )

    def _draw_current_speed(self, rect: rl.Rectangle) -> None:
        """Draw the current vehicle speed in the high-left corner, 30% smaller."""
        SPEED_FONT_SCALE = int(FONT_SIZES.current_speed * 0.7)
        UNIT_FONT_SCALE = int(FONT_SIZES.speed_unit * 0.5)

        MARGIN_X = 10
        MARGIN_Y = 10

        speed_text = str(round(self.speed))
        rl.draw_text_ex(
            self._font_bold,
            speed_text,
            rl.Vector2(rect.x + MARGIN_X, rect.y + MARGIN_Y),
            SPEED_FONT_SCALE,
            0,
            COLORS.WHITE,
        )

        unit_text = tr("km/h") if ui_state.is_metric else tr("mph")
        rl.draw_text_ex(
            self._font_medium,
            unit_text,
            rl.Vector2(rect.x + MARGIN_X, rect.y + MARGIN_Y + SPEED_FONT_SCALE + 5),
            UNIT_FONT_SCALE,
            0,
            COLORS.WHITE_TRANSLUCENT,
        )
