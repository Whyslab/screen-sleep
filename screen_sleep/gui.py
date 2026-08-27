#!/usr/bin/env python3
"""
Screen & Sleep — a settings window for idle, locking, suspend and the night filter.

The "Idle & sleep" tab edits ~/.config/hypr/hypridle.conf (two profiles: on AC
power and on battery) and restarts hypridle. The "Night filter" tab drives the
colour temperature through hyprsunset; the schedule itself is carried out by
screen-sleep-daemon.service.

Dependencies: python-gobject, gtk3, hyprsunset, hypridle, brightnessctl.
Open it from the application launcher, or bind a key to it.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
import idle
from common import (
    MAX_MINUTES,
    MAX_TEMP,
    MIN_MINUTES,
    MIN_TEMP,
    NIGHT_MARK_TEMP,
    PRESETS,
    PROFILE_LABELS,
    PROFILES,
    RAMP_INTERVAL_MIN,
    RAMP_STEP,
    STEP_LABELS,
    STEPS,
    load_config,
    save_config,
)
from gi.repository import Gdk, GLib, Gtk
from night import (
    apply_temperature,
    check_daemon_alive,
    check_hyprsunset_alive,
    read_temperature,
)

# The theme the user happens to run is not ours to assume. GTK themes paint
# controls with a background-image (usually a gradient), and a rule that sets
# only background-color leaves that image on top — the control keeps the
# theme's surface colour while our white text sits on it, which on a light
# theme is white on white. Every control below therefore clears the image too.
CSS = b"""
window {
    background-color: #0a0a0a;
}
label {
    color: #ffffff;
}
#title-label {
    font-size: 15px;
    font-weight: bold;
    color: #ffffff;
}
#value-label {
    font-size: 26px;
    font-weight: bold;
    color: #ffffff;
}
#section-label {
    font-size: 11px;
    color: #777777;
}
#status-label {
    font-size: 11px;
    color: #999999;
}
#warning-label {
    font-size: 11px;
    color: #cccccc;
}
notebook, notebook header, stack {
    background-color: #0a0a0a;
    border: none;
}
notebook header tab {
    background-color: #0a0a0a;
    padding: 6px 10px;
    border: none;
    box-shadow: none;
}
notebook header tab:checked {
    box-shadow: none;
}
notebook header tab label {
    color: #777777;
    border-bottom: 2px solid transparent;
    padding-bottom: 5px;
}
notebook header tab label.tab-active {
    color: #ffffff;
    font-weight: bold;
    border-bottom: 2px solid #ffffff;
}
stackswitcher button {
    padding: 4px 12px;
    border-color: #3a3a3a;
    background-image: none;
    background-color: #1a1a1a;
    color: #ffffff;
}
stackswitcher button label {
    color: #ffffff;
}
stackswitcher button:checked,
stackswitcher button:checked label {
    background-image: none;
    background-color: #ffffff;
    color: #0a0a0a;
    border-color: #ffffff;
}
scale trough {
    background-image: none;
    background-color: #2a2a2a;
    border-radius: 6px;
    min-height: 8px;
}
/* Themes paint the filled part of a slider with their accent colour, reaching
   it through different selectors depending on the GTK version. These cover the
   common ones; a theme with a more specific rule still wins, and the slider
   then simply picks up that accent, which is harmless. */
scale trough highlight,
scale > trough > highlight,
scale highlight,
scale trough progress {
    background-image: none;
    background-color: #3a3a3a;
    border-radius: 6px;
}
scale slider {
    background-image: none;
    background-color: #ffffff;
    border: 2px solid #0a0a0a;
    border-radius: 50%;
    min-width: 16px;
    min-height: 16px;
}
scale mark indicator {
    color: #777777;
}
scale mark label {
    color: #999999;
    font-size: 10px;
}
spinbutton, entry {
    background-image: none;
    background-color: #1a1a1a;
    color: #ffffff;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
}
spinbutton text {
    color: #ffffff;
}
spinbutton button {
    background-image: none;
    background-color: #1a1a1a;
    color: #ffffff;
    border: none;
}
checkbutton check {
    background-image: none;
    background-color: #1a1a1a;
    border: 1px solid #777777;
    border-radius: 3px;
    min-width: 14px;
    min-height: 14px;
    margin-right: 8px;
}
checkbutton check:checked {
    background-image: none;
    background-color: #ffffff;
    border-color: #ffffff;
    color: #0a0a0a;
}
button {
    background-image: none;
    background-color: #1a1a1a;
    color: #ffffff;
    border: 1px solid #ffffff;
    border-radius: 4px;
    padding: 6px 14px;
}
button label {
    color: #ffffff;
}
button:hover,
button:hover label {
    background-image: none;
    background-color: #ffffff;
    color: #0a0a0a;
}
#preset-button {
    border-color: #3a3a3a;
    padding: 4px 10px;
    font-size: 11px;
}
#preset-button:hover {
    border-color: #ffffff;
}
#caffeine-button {
    padding: 8px 14px;
    border-color: #3a3a3a;
}
#caffeine-button:checked,
#caffeine-button:checked label {
    background-image: none;
    background-color: #ffffff;
    color: #0a0a0a;
    border-color: #ffffff;
}
separator {
    background-color: #2a2a2a;
}
"""


# PROFILE_LABELS are button captions ("On AC power"); these read as a sentence.
POWER_NOW = {"ac": "on AC power", "battery": "on battery"}


def _section_label(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.set_name("section-label")
    label.set_halign(Gtk.Align.START)
    return label


class ScreenSleepWindow(Gtk.Window):
    def __init__(self, start_tab: str = "idle"):
        super().__init__(title="Screen & Sleep")
        self._start_tab = start_tab
        self.set_default_size(430, 540)
        self.set_resizable(False)
        self.connect("destroy", Gtk.main_quit)
        self.connect("key-press-event", self._on_key_press)

        self.config = load_config()
        # what is on screen now and the scheduled target are different things
        self.current_temp = read_temperature() or self.config["night"]["target_temp"]
        self._manual_override_active = self.config["night"]["manual_override"]
        self._debounce_id = None
        self._suppress_scale_signal = False
        self._last_user_move = 0.0
        self._suppress_caffeine_signal = False

        self._apply_css()
        self._build_ui()
        self._run_startup_checks()
        GLib.timeout_add_seconds(5, self._refresh_current_temp)

    def _on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    # -- building the UI --

    def _apply_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_ui(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_border_width(18)
        self.add(outer)

        notebook = Gtk.Notebook()
        self.tab_labels = [Gtk.Label(label="Idle & sleep"), Gtk.Label(label="Night filter")]
        notebook.append_page(self._build_idle_tab(), self.tab_labels[0])
        notebook.append_page(self._build_night_tab(), self.tab_labels[1])
        notebook.connect("switch-page", self._on_tab_switched)
        outer.pack_start(notebook, True, True, 0)
        self.notebook = notebook
        self._mark_active_tab(0)

        outer.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        # -- shared footer: stay awake --
        self.caffeine_button = Gtk.ToggleButton(label="Stay awake")
        self.caffeine_button.set_name("caffeine-button")
        self._suppress_caffeine_signal = True
        self.caffeine_button.set_active(idle.caffeine_active())
        self._suppress_caffeine_signal = False
        self.caffeine_button.connect("toggled", self._on_caffeine_toggled)
        outer.pack_start(self.caffeine_button, False, False, 0)

        self.caffeine_hint = _section_label("")
        outer.pack_start(self.caffeine_hint, False, False, 0)
        self._update_caffeine_hint(self.caffeine_button.get_active())

        self.status_label = Gtk.Label(label="")
        self.status_label.set_name("status-label")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_line_wrap(True)
        outer.pack_start(self.status_label, False, False, 0)

    # ---- the "Idle & sleep" tab ----

    def _build_idle_tab(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_border_width(14)

        active = idle.current_power()
        page.pack_start(_section_label(
            f"TIMING PROFILE · currently {POWER_NOW[active]}"
        ), False, False, 0)

        self.idle_stack = Gtk.Stack()
        self.idle_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.step_widgets = {}
        for profile in PROFILES:
            self.idle_stack.add_titled(
                self._build_profile_page(profile), profile, PROFILE_LABELS[profile]
            )

        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.idle_stack)
        switcher.set_halign(Gtk.Align.START)
        page.pack_start(switcher, False, False, 0)
        page.pack_start(self.idle_stack, False, False, 0)
        self.idle_stack.set_visible_child_name(active)

        page.pack_start(_section_label("PRESETS"), False, False, 0)
        preset_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for name in PRESETS:
            button = Gtk.Button(label=name)
            button.set_name("preset-button")
            button.connect("clicked", self._on_preset_clicked, name)
            preset_box.pack_start(button, False, False, 0)
        page.pack_start(preset_box, False, False, 0)

        apply_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        apply_box.set_halign(Gtk.Align.END)
        apply_button = Gtk.Button(label="Save and apply")
        apply_button.connect("clicked", self._on_idle_save_clicked)
        apply_box.pack_start(apply_button, False, False, 0)
        page.pack_start(apply_box, False, False, 0)

        return page

    def _build_profile_page(self, profile: str) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(6)
        self.step_widgets[profile] = {}

        for step in STEPS:
            entry = self.config["idle"][profile][step]
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            check = Gtk.CheckButton(label=STEP_LABELS[step])
            check.set_active(entry["enabled"])
            row.pack_start(check, False, False, 0)

            spin = Gtk.SpinButton.new_with_range(MIN_MINUTES, MAX_MINUTES, 1)
            spin.set_value(entry["minutes"])
            spin.set_numeric(True)
            spin.set_width_chars(3)
            spin.set_sensitive(entry["enabled"])
            check.connect("toggled", self._on_step_toggled, spin)

            unit = Gtk.Label(label="min")
            row.pack_end(unit, False, False, 0)
            row.pack_end(spin, False, False, 0)

            box.pack_start(row, False, False, 0)
            self.step_widgets[profile][step] = (check, spin)

        return box

    # ---- the "Night filter" tab ----

    def _build_night_tab(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_border_width(14)
        night = self.config["night"]

        page.pack_start(_section_label("CURRENTLY ON SCREEN"), False, False, 0)

        self.value_label = Gtk.Label(label=f"{self.current_temp} K")
        self.value_label.set_name("value-label")
        page.pack_start(self.value_label, False, False, 0)

        self.scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, MIN_TEMP, MAX_TEMP, 50
        )
        self.scale.set_inverted(True)  # right = warmer (fewer K)
        self.scale.set_value(self.current_temp)
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.add_mark(NIGHT_MARK_TEMP, Gtk.PositionType.BOTTOM, "good for sleep")
        self.scale.add_mark(MAX_TEMP, Gtk.PositionType.BOTTOM, "off")
        self.scale.connect("value-changed", self._on_scale_changed)
        page.pack_start(self.scale, False, False, 0)

        target_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        target_box.pack_start(_section_label("TARGET AT NIGHT"), False, False, 0)
        self.night_target_spin = Gtk.SpinButton.new_with_range(MIN_TEMP, MAX_TEMP, 100)
        self.night_target_spin.set_value(night["target_temp"])
        self.night_target_spin.set_numeric(True)
        self.night_target_spin.set_width_chars(5)
        target_box.pack_end(Gtk.Label(label="K"), False, False, 0)
        target_box.pack_end(self.night_target_spin, False, False, 0)
        page.pack_start(target_box, False, False, 0)

        page.pack_start(_section_label(
            f"GRADUAL WARM-UP STARTS AT ({RAMP_STEP}K / {RAMP_INTERVAL_MIN} min)"
        ), False, False, 0)
        self.on_hour_spin, self.on_minute_spin = self._build_time_row(
            page, night["on_hour"], night["on_minute"]
        )

        page.pack_start(_section_label("SNAPS BACK OFF AT"), False, False, 0)
        self.off_hour_spin, self.off_minute_spin = self._build_time_row(
            page, night["off_hour"], night["off_minute"]
        )

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_halign(Gtk.Align.END)

        off_now_button = Gtk.Button(label="Turn off now")
        off_now_button.connect("clicked", self._on_off_clicked)
        button_box.pack_start(off_now_button, False, False, 0)

        save_button = Gtk.Button(label="Save")
        save_button.connect("clicked", self._on_night_save_clicked)
        button_box.pack_start(save_button, False, False, 0)

        page.pack_start(button_box, False, False, 0)
        return page

    def _build_time_row(self, parent, hour: int, minute: int):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hour_spin = Gtk.SpinButton.new_with_range(0, 23, 1)
        hour_spin.set_value(hour)
        hour_spin.set_numeric(True)
        hour_spin.set_width_chars(2)
        row.pack_start(hour_spin, False, False, 0)
        row.pack_start(Gtk.Label(label=":"), False, False, 0)
        minute_spin = Gtk.SpinButton.new_with_range(0, 59, 1)
        minute_spin.set_value(minute)
        minute_spin.set_numeric(True)
        minute_spin.set_width_chars(2)
        row.pack_start(minute_spin, False, False, 0)
        parent.pack_start(row, False, False, 0)
        return hour_spin, minute_spin

    def _on_tab_switched(self, notebook, page, index):
        self._mark_active_tab(index)

    def _mark_active_tab(self, index: int):
        for position, label in enumerate(self.tab_labels):
            context = label.get_style_context()
            if position == index:
                context.add_class("tab-active")
            else:
                context.remove_class("tab-active")

    def _run_startup_checks(self):
        alive, message = check_hyprsunset_alive()
        if not alive:
            self._set_warning(message)
            return
        if not idle.hypridle_running():
            self._set_warning("hypridle is not running — idle and locking will not work")
            return
        daemon_ok, daemon_message = check_daemon_alive()
        if not daemon_ok:
            self._set_warning(daemon_message)

    # -- collecting the config from the widgets --

    def _collect_config(self) -> dict:
        config = {
            "night": {
                "target_temp": int(self.night_target_spin.get_value()),
                "on_hour": int(self.on_hour_spin.get_value()),
                "on_minute": int(self.on_minute_spin.get_value()),
                "off_hour": int(self.off_hour_spin.get_value()),
                "off_minute": int(self.off_minute_spin.get_value()),
                "manual_override": self._manual_override_active,
            },
            "idle": {},
        }
        for profile in PROFILES:
            config["idle"][profile] = {
                step: {
                    "enabled": check.get_active(),
                    "minutes": int(spin.get_value()),
                }
                for step, (check, spin) in self.step_widgets[profile].items()
            }
        return config

    def _active_profile(self) -> str:
        """Which profile is open. Before the window is shown the stack has no page."""
        name = self.idle_stack.get_visible_child_name()
        return name if name in PROFILES else idle.current_power()

    def _out_of_order(self, profile: dict) -> bool:
        enabled = [profile[step]["minutes"] for step in STEPS if profile[step]["enabled"]]
        return any(a > b for a, b in zip(enabled, enabled[1:], strict=False))

    # -- handlers: idle --

    def _on_step_toggled(self, check, spin):
        spin.set_sensitive(check.get_active())

    def _on_preset_clicked(self, button, name: str):
        profile = self._active_profile()
        for step, minutes in PRESETS[name].items():
            check, spin = self.step_widgets[profile][step]
            check.set_active(minutes is not None)
            if minutes is not None:
                spin.set_value(minutes)
        self._set_status(f"Preset \"{name}\" filled into \"{PROFILE_LABELS[profile]}\" — "
                         f"press \"Save and apply\"")

    def _on_idle_save_clicked(self, button):
        config = self._collect_config()
        save_config(config)
        self.config = config

        power = idle.current_power()
        ok, message = idle.apply_profile(config, power)
        if not ok:
            self._set_warning(message)
            return

        edited = self._active_profile()
        text = (f"Saved. The \"{PROFILE_LABELS[power]}\" profile is in effect: "
                + self._describe(config["idle"][power]))
        if edited != power:
            text += f" · \"{PROFILE_LABELS[edited]}\" takes over when the power source changes"
        if self._out_of_order(config["idle"][power]):
            text += " · note: the steps are not in increasing order of time"
            self._set_warning(text)
        else:
            self._set_status(text)

    @staticmethod
    def _describe(profile: dict) -> str:
        parts = []
        for step in STEPS:
            entry = profile[step]
            parts.append(f"{STEP_LABELS[step].lower()} "
                         + (f"{entry['minutes']} min" if entry["enabled"] else "off"))
        return ", ".join(parts)

    # -- handlers: stay awake --

    def _update_caffeine_hint(self, active: bool):
        self.caffeine_hint.set_text(
            "The screen will not dim or lock while this is pressed"
            if active else "Idle behaves as configured above"
        )

    def _on_caffeine_toggled(self, button):
        if self._suppress_caffeine_signal:
            return
        state = button.get_active()
        ok, message = idle.caffeine_set(state)
        if not ok:
            self._set_warning(message)
            self._suppress_caffeine_signal = True
            button.set_active(not state)
            self._suppress_caffeine_signal = False
            return
        self._update_caffeine_hint(state)
        self._set_status("Idle is suspended — the machine will not sleep or lock"
                         if state else "Normal idle behaviour")

    # -- handlers: night filter --

    def _on_scale_changed(self, scale):
        value = int(scale.get_value())
        self.current_temp = value
        self.value_label.set_text(f"{value} K")

        if self._suppress_scale_signal:
            return
        self._last_user_move = time.monotonic()

        if not self._manual_override_active:
            self._manual_override_active = True
            self._persist_override_flag(True)

        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(120, self._apply_debounced, value)

    def _apply_debounced(self, value):
        ok, message = apply_temperature(value)
        if not ok:
            self._set_warning(message)
        else:
            self._set_status("")
        self._debounce_id = None
        return False

    def _on_off_clicked(self, button):
        self.scale.set_value(MAX_TEMP)  # this fires _on_scale_changed itself

    def _refresh_current_temp(self) -> bool:
        """The daemon changes the temperature on its own — keep the window in sync."""
        if self._debounce_id or time.monotonic() - self._last_user_move < 10:
            return True  # the user just moved the slider; stay out of the way
        actual = read_temperature()
        if actual is not None and actual != self.current_temp:
            self._suppress_scale_signal = True
            self.scale.set_value(actual)
            self._suppress_scale_signal = False
        return True

    def _on_night_save_clicked(self, button):
        self._manual_override_active = False
        config = self._collect_config()
        save_config(config)
        self.config = config
        night = config["night"]
        self._set_status(
            f"Saved: target at night {night['target_temp']} K · warm-up from "
            f"{night['on_hour']:02d}:{night['on_minute']:02d} · off at "
            f"{night['off_hour']:02d}:{night['off_minute']:02d}"
        )

    def _persist_override_flag(self, value: bool):
        config = load_config()
        config["night"]["manual_override"] = value
        save_config(config)

    # -- status line --

    def _set_status(self, message: str):
        self.status_label.set_name("status-label")
        self.status_label.set_text(message)

    def _set_warning(self, message: str):
        self.status_label.set_name("warning-label")
        self.status_label.set_text(message)


def main():
    start_tab = "night" if "--night" in sys.argv else "idle"
    window = ScreenSleepWindow(start_tab)
    window.show_all()
    # set_current_page only works after show_all — before that the pages are hidden
    window.notebook.set_current_page(1 if start_tab == "night" else 0)
    Gtk.main()


if __name__ == "__main__":
    main()
