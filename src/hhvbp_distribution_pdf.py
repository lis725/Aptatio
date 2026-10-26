"""Self-contained agency PDF from one already-computed distribution response.

This module never imports an allocator. Charts use the same vector scene as
optional developer images. A failed build cannot leave a partial target PDF.
"""
from __future__ import annotations

from html import escape
import math
import os
from pathlib import Path
import re
import tempfile

import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont, TTFError
from reportlab.platypus import (
    Flowable, LongTable, PageBreak, Paragraph, SimpleDocTemplate, TableStyle,
)

INK = colors.HexColor('#193447')
MUTED = colors.HexColor('#4b6170')
PALE = colors.HexColor('#edf3f6')
RULE = colors.HexColor('#d5e0e7')
PAGE_WIDTH, PAGE_HEIGHT = letter
MARGIN = 40
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN


def report_fonts():
    """Portable, embedded ReportLab fonts, with a built-in fallback."""
    regular, bold = 'AptatioVera', 'AptatioVeraBold'
    if regular in pdfmetrics.getRegisteredFontNames():
        return regular, bold
    directory = Path(reportlab.__file__).parent / 'fonts'
    try:
        pdfmetrics.registerFont(TTFont(regular, str(directory / 'Vera.ttf')))
        pdfmetrics.registerFont(TTFont(bold, str(directory / 'VeraBd.ttf')))
        pdfmetrics.registerFontFamily(regular, normal=regular, bold=bold,
                                      italic=regular, boldItalic=bold)
        return regular, bold
    except (OSError, ValueError, TTFError):
        return 'Helvetica', 'Helvetica-Bold'


def _text(value):
    if value is None:
        return 'Not recorded'
    # Escape all supplied labels before passing them to ReportLab's markup parser.
    clean = ''.join(c for c in str(value) if ord(c) >= 32 or c in '\n\t')
    return escape(clean, quote=False).replace('\n', '<br/>')


def _num(value, digits=6):
    if value is None:
        return '-'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, (float, int)):
        if not math.isfinite(value):
            return 'Unavailable'
        return f'{value:.{digits}g}'
    return str(value)


def _styles():
    regular, bold = report_fonts()
    return {
        'title': ParagraphStyle('ReportTitle', fontName=bold, fontSize=21,
                                leading=25, textColor=INK, spaceAfter=10),
        'section': ParagraphStyle('ReportSection', fontName=bold, fontSize=14,
                                  leading=18, textColor=INK, spaceBefore=12,
                                  spaceAfter=7, keepWithNext=True),
        'sub': ParagraphStyle('ReportSubsection', fontName=bold, fontSize=11,
                              leading=14, textColor=INK, spaceBefore=9,
                              spaceAfter=5, keepWithNext=True),
        'body': ParagraphStyle('ReportBody', fontName=regular, fontSize=10.2,
                               leading=14, textColor=INK, spaceAfter=6,
                               splitLongWords=True),
        'small': ParagraphStyle('ReportSmall', fontName=regular, fontSize=9,
                                leading=12, textColor=MUTED, spaceAfter=5,
                                splitLongWords=True),
        'cell': ParagraphStyle('ReportCell', fontName=regular, fontSize=9,
                               leading=11.5, textColor=INK, splitLongWords=True),
        'head': ParagraphStyle('ReportHeadCell', fontName=bold, fontSize=9,
                               leading=11.5, textColor=INK, splitLongWords=True),
    }


def _p(value, styles, kind='body', *, emphasize_need_score=False):
    text = _text(value)
    if emphasize_need_score:
        text = re.sub(r'\bNeed Scores?\b', r'<b>\g<0></b>', text)
    return Paragraph(text, styles[kind])


def _table(headers, rows, widths, styles, *, small_padding=False):
    data = [[_p(v, styles, 'head') for v in headers]]
    data.extend([[v if isinstance(v, Flowable) else _p(v, styles, 'cell') for v in row] for row in rows])
    table = LongTable(data, colWidths=widths, repeatRows=1, hAlign='LEFT')
    padding = 3 if small_padding else 5
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PALE),
        ('LINEBELOW', (0, 0), (-1, 0), .7, RULE),
        ('LINEBELOW', (0, 1), (-1, -1), .3, RULE),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), padding),
        ('BOTTOMPADDING', (0, 0), (-1, -1), padding),
    ]))
    return table


def _identity(card):
    name, identifier = card.get('clinician_name'), card.get('clinician_id')
    if name is None and identifier is None:
        return 'No clinician (null)'
    if name is None:
        return f'ID {identifier}'
    return f'{name} (ID {identifier})' if identifier is not None else str(name)


def _option_label(option):
    return option.get('option_label') or f"Option {option.get('option_rank', '?')}"













class FactorDirection(Flowable):
    """Vector arrow/symbol only; its meaning is explained in the table legend."""
    def __init__(self, row, styles):
        super().__init__()
        from hhvbp_factor_interpretation import DIRECTION_STYLE
        self.direction = row.get('direction', 'unknown')
        self.label, self.symbol, self.color = DIRECTION_STYLE.get(self.direction, DIRECTION_STYLE['unknown'])
        self.width = 170
        self.height = 18

    def wrap(self, available_width, available_height):
        self.width = available_width
        self.height = 18
        return self.width, self.height

    def draw(self):
        canvas = self.canv
        canvas.saveState()
        canvas.setFillColor(colors.HexColor(self.color))
        canvas.setStrokeColor(colors.HexColor(self.color))
        canvas.translate(2, max(0, (self.height - 16) / 2))
        if self.symbol in ('up', 'down'):
            path = canvas.beginPath()
            points = [(4,0),(10,0),(10,8),(14,8),(7,16),(0,8),(4,8)]
            if self.symbol == 'down':
                points = [(x,16-y) for x,y in points]
            path.moveTo(*points[0])
            for point in points[1:]:path.lineTo(*point)
            path.close();canvas.drawPath(path,fill=1,stroke=0)
        elif self.symbol == 'neutral':
            canvas.setLineWidth(2.3);canvas.line(0,8,14,8)
        elif self.symbol == 'mixed':
            canvas.setLineWidth(1.8)
            canvas.line(3,1,3,15);canvas.line(0,11,3,15);canvas.line(6,11,3,15)
            canvas.line(11,1,11,15);canvas.line(8,5,11,1);canvas.line(14,5,11,1)
        else:
            canvas.setLineWidth(1.5);canvas.circle(7,8,6,stroke=1,fill=0)
            canvas.circle(7,8,1.1,stroke=0,fill=1)
        canvas.restoreState()


def _factor_table(shared, styles):
    profile = shared.get('patient_profile_summary', {})
    annotations = shared.get('factor_interpretations', {})
    factors = annotations.get('factors', [])
    rows=[]
    for factor in factors:
        key=factor['factor_key']
        label=profile.get('factor_labels',{}).get(key,key.replace('_',' ').title())
        normalized=profile.get('readable_factors',{}).get(key,factor.get('normalized_value'))
        supplied=factor.get('supplied_value')
        value=(f'Provided: {supplied}\nNormalized: {normalized}' if str(supplied)!=str(normalized)
               else str(normalized) if normalized is not None else 'Not supplied')
        rows.append((label,value,FactorDirection(factor,styles)))
    rows.append(('Patient ZIP',profile.get('patient_zip') or 'Not supplied',''))
    return _table(['Factor','Provided / normalized value','Interpretation in the current model'],rows,
                  [155,175,CONTENT_WIDTH-330],styles)


def _scorecard_table(card, styles):
    scores=card.get('scorecard',{})
    wanted=card.get('highlighted_metric_keys',scores.get('highlighted_metric_keys',[]))[:3]
    keyed={row['metric_key']:row for row in scores.get('metrics',[])}
    rows=[keyed[key] for key in wanted if key in keyed]
    if not rows:return None
    from hhvbp_scorecard_evidence import MISSING
    scopes=['national']
    scopes.extend(scope for scope in ('state','organization')
                  if any(row.get(scope+'_display',MISSING)!=MISSING for row in rows))
    headers=['Metric','Clinician scorecard'] + [scope.title()+(' average' if scope!='organization' else ' value') for scope in scopes]
    widths=[145,79]+[(CONTENT_WIDTH-224)/len(scopes)]*len(scopes)
    data=[]
    for row in rows:
        direction={'lower_is_better':'Lower is favorable','higher_is_better':'Higher is favorable'}.get(row.get('direction'),'Direction not established')
        data.append([row['label']+'\n'+direction,row.get('clinician_display',MISSING)]+
                    [row.get(scope+'_display',MISSING) for scope in scopes])
    return _table(headers,data,widths,styles,small_padding=True)


def _sponsor_story(response, styles, *, option_rank=None):
    """Render the shared content contract; no allocation or narrative recomputation."""
    from hhvbp_report_content import get_agency_report_view
    from hhvbp_distribution_chart import build_chart_drawing
    view=get_agency_report_view(response,option_rank=option_rank)
    styles=dict(styles)
    styles['body']=ParagraphStyle('AgencyCompactBody',parent=styles['body'],spaceAfter=4,allowWidows=0)
    shared=view.get('shared');options=view['options']
    title='Patient assignment report' if view['view_kind']=='combined' else 'Patient assignment report - option '+str(option_rank)
    story=[_p(title,styles,'title')]
    if shared:
        position=shared.get('patient_distribution',{})
        presentation=response['distribution_explanation'].get('presentation',{})
        story.append(_p('Decision overview and patient position',styles,'section'))
        story.append(_p('Modeled-need score: '+str(presentation.get('score',_num(position.get('patient_raw_score'))))+
                        '    |    Percentile: '+str(presentation.get('percentile','Unavailable')),styles,'sub'))
        drawing=build_chart_drawing(position.get('chart_data'), include_reference_note=False)
        scale=CONTENT_WIDTH/drawing.width;drawing.scale(scale,scale)
        drawing.width*=scale;drawing.height*=scale;story.append(drawing)
        regions=shared.get('policy_regions',{})
        if regions.get('left') and regions.get('right'):
            left,right=regions['left'],regions['right']
            story.append(_table([left['title'],right['title']],[[left['boundary'],right['boundary']],[left['policy'],right['policy']]],
                                [CONTENT_WIDTH/2,CONTENT_WIDTH/2],styles))
        if regions.get('notice'):story.append(_p(regions['notice'],styles,'small'))
        narrative=shared.get('narrative','Distribution evidence unavailable.')
        # Keep the frozen wording intact, separating definition, policy, and patient position.
        for key in ('boundary_sentence','position_sentence'):
            sentence=shared.get(key)
            if sentence:
                narrative=narrative.replace(' '+sentence,'\n\n'+sentence,1)
        for paragraph in narrative.split('\n\n'):
            story.append(_p(paragraph,styles,'small',emphasize_need_score=True))
        story.append(_p(shared.get('legend',''),styles,'small'))
        if position.get('chart_data') is None:
            for warning in position.get('warnings',[]):story.append(_p(warning,styles,'small'))
        story.append(PageBreak())
        story.append(_p('Known patient information',styles,'section'))
        story.append(_factor_table(shared,styles))
        legend=shared.get('factor_interpretations',{}).get('legend')
        if legend:story.append(_p(legend,styles,'small'))
    story.append(_p('Returned assignment options' if option_rank is None else 'Selected report option',styles,'section'))
    if not options:
        story.append(_p('No assignment options were requested or returned.',styles))
    elif option_rank is None:
        overview=[[_option_label(o)]+[_identity(o.get('team',{}).get(d,{})) for d in ('RN','PT','OT')] for o in options]
        story.append(_table(['Option','RN','PT','OT'],overview,[103,143,143,143],styles))
    for option in options:
        story.append(_p(_option_label(option)+' - reasons',styles,'section'))
        for discipline,card in option.get('team',{}).items():
            story.append(_p(discipline+': '+_identity(card),styles,'sub'))
            paragraphs=card.get('paragraphs') or [card.get('selection_text', 'Selection evidence unavailable.')]
            # Individual paragraphs and table rows split naturally. Never keep the
            # complete clinician section together, which caused large empty pages.
            for paragraph in paragraphs[:2]:
                if paragraph:story.append(_p(paragraph,styles))
            table=_scorecard_table(card,styles)
            if table is not None:story.append(table)
            for paragraph in paragraphs[2:]:
                if paragraph:story.append(_p(paragraph,styles))
            adjusted=[row for row in card.get('scorecard',{}).get('metrics',[]) if row.get('scoring_adjustment_applied')]
            if adjusted:
                story.append(_p('Configured scoring adjustments are separate from these original scorecard values; adjusted inputs remain in the structured evidence.',styles,'small'))
    return story

def write_distribution_pdf(response: dict, output_path: str | Path, *, option_rank: int | None = None) -> Path:
    """Render one existing response without allocation or mutation; return its PDF path.

    Build beside the destination and replace it only after ReportLab has completed.
    The caller can keep the already serialized response if PDF generation fails.
    """
    target = Path(output_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    regular, _ = report_fonts()
    decision_id = str(response.get('decision_id', 'Not recorded'))
    descriptor, temporary = tempfile.mkstemp(prefix='.' + target.stem + '-', suffix='.pdf', dir=target.parent)
    os.close(descriptor)
    try:
        document = SimpleDocTemplate(
            temporary, pagesize=letter, rightMargin=MARGIN, leftMargin=MARGIN,
            topMargin=34, bottomMargin=38, title='Aptatio patient assignment report',
            author='Aptatio', subject='Decision ' + decision_id,
            pageCompression=1,
        )

        def footer(canvas, doc):
            canvas.saveState()
            canvas.setStrokeColor(RULE)
            canvas.line(MARGIN, 30, PAGE_WIDTH - MARGIN, 30)
            canvas.setFillColor(MUTED)
            canvas.setFont(regular, 9)
            canvas.drawString(MARGIN, 17, 'Aptatio')
            canvas.drawRightString(PAGE_WIDTH - MARGIN, 17, f'Page {doc.page}')
            canvas.restoreState()

        story = _sponsor_story(response, styles, option_rank=option_rank)
        document.build(story, onFirstPage=footer, onLaterPages=footer)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target
