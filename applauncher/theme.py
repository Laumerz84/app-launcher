"""Colours for the light and dark looks (Windows 11 style)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    dark: bool
    window: str        # window background
    card: str          # app row
    card_border: str
    card_hover: str
    card_hover_border: str
    card_pressed: str
    text: str
    muted: str
    accent: str        # primary button + focus ring
    accent_hover: str
    accent_pressed: str
    on_accent: str     # text on the primary button
    secondary: str     # secondary button fill
    secondary_hover: str
    secondary_pressed: str
    running: str       # the "running" dot / label
    error: str
    keycap: str
    keycap_border: str
    danger: str        # the armed "Click again to stop" button
    danger_hover: str
    danger_pressed: str
    on_danger: str


LIGHT = Palette(
    name="light", dark=False,
    window="#f3f3f3",
    card="#ffffff", card_border="#e3e3e3",
    card_hover="#f9fbfe", card_hover_border="#c9d6e8",
    card_pressed="#eef3fa",
    text="#1a1a1a", muted="#5f5f5f",
    accent="#0067c0", accent_hover="#1975c9", accent_pressed="#005399", on_accent="#ffffff",
    secondary="#fbfbfb", secondary_hover="#f6f6f6", secondary_pressed="#ececec",
    running="#15803d", error="#c42b1c",
    keycap="#f3f3f3", keycap_border="#dcdcdc",
    danger="#c42b1c", danger_hover="#d13a2b", danger_pressed="#a52315", on_danger="#ffffff",
)

DARK = Palette(
    name="dark", dark=True,
    window="#202020",
    card="#2b2b2b", card_border="#3a3a3a",
    card_hover="#333333", card_hover_border="#4b5563",
    card_pressed="#2a2a2a",
    text="#f5f5f5", muted="#a6a6a6",
    accent="#4cc2ff", accent_hover="#6bcdff", accent_pressed="#3aa5dc", on_accent="#00263d",
    secondary="#2f2f2f", secondary_hover="#383838", secondary_pressed="#292929",
    running="#4ade80", error="#ff8a80",
    keycap="#242424", keycap_border="#454545",
    danger="#ff8a80", danger_hover="#ff9f97", danger_pressed="#e67a70", on_danger="#3a0a06",
)


def palette_for(dark: bool) -> Palette:
    return DARK if dark else LIGHT
