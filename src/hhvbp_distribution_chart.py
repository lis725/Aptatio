"""Shared vector chart geometry for agency PDFs; no image sidecar files."""
from __future__ import annotations
import textwrap
from reportlab.graphics.shapes import Drawing, Line, Rect, String, Polygon, PolyLine
from reportlab.lib.colors import HexColor

WIDTH, HEIGHT = 1200, 700
LEFT, RIGHT, TOP, BOTTOM = 95, 1135, 190, 510
PATIENT_COLOR = "#4253a6"
THRESHOLD_COLOR = "#c62828"


def raw_x(value):
    return LEFT + (RIGHT - LEFT) * value


def chart_scene(chart: dict | None, *, include_reference_note: bool = True) -> list[tuple]:
    """Vector scene geometry. All marker x coordinates are exact raw scores."""
    height = HEIGHT if include_reference_note else 620
    scene = [("rect", 0, 0, WIDTH, height, "#ffffff"),
             ("text", 40, 28, "Patient position in the modeled distribution", 30, "#152e43")]
    if not chart:
        scene.append(("text", 40, 115, "Reference distribution unavailable; assignments are retained.", 20, "#704600"))
        return scene
    curve = chart["density_curve"]
    scene += [("dash", 45, 93, 85, 93, PATIENT_COLOR, 3),
              ("text", 98, 80, "Incoming patient", 24, "#152e43")]
    threshold = chart.get("threshold_marker")
    if threshold:
        scene += [("line", 390, 93, 435, 93, THRESHOLD_COLOR, 4),
                  ("text", 448, 80, threshold["label"], 24, "#152e43")]
    if chart["raw_score_override_used"] or chart["percentile_override_used"]:
        scene.append(("text", 40, 122, "Test override active. Marker shows the actual raw score.", 22, "#704600"))
    if threshold and threshold["kind"] == "quantile_guide":
        scene.append(("text", 40, 122, "Guide only: this threshold has no unique raw-score boundary.", 22, "#704600"))
    scene.append(("text", LEFT, TOP-32, "Density", 22, "#405c70"))
    for fraction in (0.0, 0.5, 1.0):
        y = BOTTOM - (BOTTOM-TOP) * fraction
        scene.append(("line", LEFT, y, RIGHT, y, "#e6edf2", 1))
    if curve["status"] == "ready":
        maximum = max(curve["density"]) * 1.12
        points = [(raw_x(x), BOTTOM - (BOTTOM-TOP) * y/maximum) for x,y in zip(curve["x"],curve["density"])]
        scene += [("polygon", [(LEFT,BOTTOM)] + points + [(RIGHT,BOTTOM)], "#e1edf2"),
                  ("polyline", points, "#527d92", 2)]
    elif curve["status"] == "point_mass":
        x = raw_x(curve["point"])
        scene += [("line", x, TOP, x, BOTTOM, "#527d92", 6),
                  ("text", 180, 235, "All reference profiles share one score; no smooth density is estimable.", 19, "#405c70")]
    else:
        scene.append(("text", 180, 235, "Reference density unavailable; assignments are retained.", 19, "#704600"))
    scene.append(("line", LEFT, BOTTOM, RIGHT, BOTTOM, "#152e43", 1))
    for i in range(11):
        x = raw_x(i/10)
        scene += [("line", x, BOTTOM, x, BOTTOM+5, "#152e43", 1),
                  ("text", x-11, BOTTOM+12, f"{i/10:.1f}", 21, "#152e43")]
    if threshold:
        x = raw_x(threshold["raw_score"])
        # The wider solid threshold remains visible behind a coincident patient dash.
        scene.append(("line", x, TOP-16, x, BOTTOM, THRESHOLD_COLOR, 4))
    marker = raw_x(chart["patient_raw_score"])
    scene += [("dash", marker, TOP, marker, BOTTOM, PATIENT_COLOR, 3),
              ("text", 493, 551, "Modeled-need score", 24, "#152e43"),
              ("text", LEFT, 583, "Lower modeled need", 21, "#405c70"),
              ("text", RIGHT-210, 583, "Higher modeled need", 21, "#405c70")]
    if include_reference_note:
        for i,line in enumerate(textwrap.wrap(chart["reference_note"], width=94)):
            scene.append(("text", 40, 621+i*28, line, 22, "#405c70"))
    return scene


def build_chart_drawing(chart: dict | None, *, include_reference_note: bool = True) -> Drawing:
    """Build the vector drawing embedded directly in the agency PDF."""
    height = HEIGHT if include_reference_note else 620
    drawing = Drawing(WIDTH, height)
    for item in chart_scene(chart, include_reference_note=include_reference_note):
        kind = item[0]
        if kind == "rect":
            _,x,y,w,h,color = item
            drawing.add(Rect(x,height-y-h,w,h,fillColor=HexColor(color),strokeColor=None))
        elif kind in ("polygon","polyline"):
            points,color = item[1:3]
            flat = [coordinate for x,y in points for coordinate in (x,height-y)]
            drawing.add(Polygon(flat,fillColor=HexColor(color),strokeColor=None) if kind == "polygon" else
                        PolyLine(flat,strokeColor=HexColor(color),strokeWidth=item[3]))
        elif kind in ("line","dash"):
            _,x,y,x2,y2,color,width = item
            attrs = {"strokeDashArray": [8,6]} if kind == "dash" else {}
            drawing.add(Line(x,height-y,x2,height-y2,strokeColor=HexColor(color),strokeWidth=width,**attrs))
        else:
            _,x,y,text,size,color = item
            drawing.add(String(x,height-y-size*.8,text,fontName="Helvetica",fontSize=size,fillColor=HexColor(color)))
    return drawing
