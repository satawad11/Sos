"""Monthly PDF report for the รพ.สต. / อบต. — A4, Sarabun (Thai government document font), Buddhist Era dates.

Built from reports.summary() + the case list + the monthly button self-test, so the numbers always match the
dashboard.  Thai text is shaped with HarfBuzz (fpdf2 + uharfbuzz) so vowels and tone marks sit correctly.
Organisation names, signers and up to two logos come from settings "org" / the logo store (Settings page).
"""
from __future__ import annotations
import io, os
from datetime import date, datetime
from fpdf import FPDF
from sqlalchemy.orm import Session
from . import reports, selftest, settings_store
from .gateways import _aware
from .models import Asset, utcnow

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
NAVY, BLUE, RED, GREEN, AMBER, GREY, LINE, SOFT = (15, 27, 45), (29, 78, 216), (220, 38, 38), (22, 163, 74), (217, 119, 6), (100, 116, 139), (220, 226, 234), (243, 245, 249)


def _dur(s) -> str:
    if s is None: return "-"
    s = round(float(s))
    return f"{s} วินาที" if s < 90 else f"{s // 60}:{s % 60:02d} นาที"


def _pct(v) -> str:
    return "-" if v is None else f"{v:.1f}%".replace(".0%", "%")


class Doc(FPDF):
    def __init__(self, footer: str):
        super().__init__("P", "mm", "A4")
        self.foot = footer
        self.add_font("Sarabun", "", os.path.join(FONT_DIR, "Sarabun-Regular.ttf"))
        self.add_font("Sarabun", "B", os.path.join(FONT_DIR, "Sarabun-Bold.ttf"))
        self.set_text_shaping(True)
        self.set_auto_page_break(True, 16)
        self.set_margins(16, 14, 16)
        self.alias_nb_pages()

    def footer(self):
        self.set_y(-12); self.set_font("Sarabun", "", 9); self.set_text_color(*GREY)
        self.cell(0, 5, self.foot, align="L")
        self.set_x(self.l_margin); self.cell(0, 5, f"หน้า {self.page_no()}/{{nb}}", align="R")

    # ---- helpers ----
    def fit(self, text: str, w: float) -> str:
        text = str(text if text is not None else "")
        if self.get_string_width(text) <= w - 2: return text
        while text and self.get_string_width(text + "…") > w - 2: text = text[:-1]
        return text + "…"

    def h1(self, text: str):
        if self.get_y() > 250: self.add_page()
        self.ln(3); self.set_font("Sarabun", "B", 15); self.set_text_color(*NAVY)
        self.set_fill_color(*BLUE); self.rect(self.l_margin, self.get_y() + 1.2, 1.6, 6.2, "F")
        self.set_x(self.l_margin + 4); self.cell(0, 8.5, text, new_x="LMARGIN", new_y="NEXT"); self.ln(1)

    def table(self, head: list[str], rows: list[list], widths: list[float], align: list[str] | None = None, size=11, zebra=True,
              colors: list | None = None):
        align = align or ["L"] * len(head)
        if self.get_y() > 272 - 7 * min(len(rows), 3) - 8: self.add_page()     # never strand a header at the page bottom
        def header():
            self.set_font("Sarabun", "B", size - 0.5); self.set_fill_color(*SOFT); self.set_text_color(*GREY); self.set_draw_color(*LINE)
            for h, w, a in zip(head, widths, align): self.cell(w, 7.5, self.fit(h, w), border="B", align=a, fill=True)
            self.ln()
        header()
        self.set_font("Sarabun", "", size)
        for i, r in enumerate(rows):
            if self.get_y() > 272: self.add_page(); header(); self.set_font("Sarabun", "", size)
            fill = zebra and i % 2 == 1
            self.set_fill_color(250, 251, 253)
            for j, (v, w, a) in enumerate(zip(r, widths, align)):
                c = colors[i][j] if colors and colors[i] and colors[i][j] else NAVY
                self.set_text_color(*c)
                self.cell(w, 7, self.fit(v, w), border="B", align=a, fill=fill)
            self.ln()
        self.set_text_color(*NAVY)

    def kpis(self, items: list[tuple[str, str, str, tuple]]):
        """Row of tiles: (label, value, sub, colour)."""
        n = len(items); gap = 3; w = (self.epw - gap * (n - 1)) / n; y = self.get_y(); x = self.l_margin
        for lbl, val, sub, col in items:
            self.set_draw_color(*LINE); self.set_fill_color(255, 255, 255); self.rect(x, y, w, 22, "DF", round_corners=True, corner_radius=2)
            self.set_fill_color(*col); self.rect(x, y + 3, 1.3, 16, "F")
            self.set_xy(x + 4, y + 2); self.set_font("Sarabun", "", 9.5); self.set_text_color(*GREY); self.cell(w - 6, 5, self.fit(lbl, w - 6))
            self.set_xy(x + 4, y + 7); self.set_font("Sarabun", "B", 17); self.set_text_color(*col); self.cell(w - 6, 8, self.fit(val, w - 6))
            self.set_xy(x + 4, y + 15.5); self.set_font("Sarabun", "", 8.5); self.set_text_color(*GREY); self.cell(w - 6, 4.5, self.fit(sub, w - 6))
            x += w + gap
        self.set_text_color(*NAVY); self.set_y(y + 25)

    def bars(self, labels: list[str], values: list[int], h=34, color=BLUE):
        n = len(values); top = max(values) or 1; w = self.epw / n; x0 = self.l_margin; y0 = self.get_y() + h
        self.set_draw_color(*LINE); self.line(x0, y0, x0 + self.epw, y0)
        for i, v in enumerate(values):
            bh = (h - 6) * v / top
            if v:
                self.set_fill_color(*color); self.rect(x0 + i * w + w * .18, y0 - bh, w * .64, bh, "F")
                self.set_font("Sarabun", "", 7.5); self.set_text_color(*NAVY)
                self.set_xy(x0 + i * w, y0 - bh - 4.2); self.cell(w, 4, str(v), align="C")
            self.set_font("Sarabun", "", 7.5); self.set_text_color(*GREY)
            self.set_xy(x0 + i * w, y0 + .8); self.cell(w, 4, labels[i], align="C")
        self.set_text_color(*NAVY); self.set_y(y0 + 7)


def _logo(db: Session, slot: str):
    a = db.get(Asset, f"logo_{slot}")
    return io.BytesIO(a.data) if a else None


def build(db: Session, month: str, names: bool, actor: str) -> bytes:
    start, end, d1, d2 = reports.parse_range(month, None, None)
    s = reports.summary(db, start, end, d1, d2, names=names)
    org = settings_store.get(db, "org")
    st = selftest.status(db, month, names=names)
    cs = reports._cases(db, start, end)
    now = utcnow()
    mth = selftest.month_th(month)
    pdf = Doc(f"ระบบแจ้งเหตุฉุกเฉิน · พิมพ์เมื่อ {reports.be_dt(now)} โดย {actor}")
    pdf.add_page()

    # ---- letterhead ----
    top = pdf.get_y(); logos = 0
    for slot, x in (("left", pdf.l_margin), ("right", pdf.w - pdf.r_margin - 22)):
        img = _logo(db, slot)
        if img:
            try: pdf.image(img, x=x, y=top, h=22, keep_aspect_ratio=True, w=22); logos += 1
            except Exception: pass
    pad = 25 if logos else 0                      # keep the title block clear of the logos
    tw = pdf.epw - 2 * pad
    def line(text, size, bold=False, color=NAVY, h=7):
        pdf.set_font("Sarabun", "B" if bold else "", size); pdf.set_text_color(*color)
        while size > 10 and pdf.get_string_width(text) > tw - 2:
            size -= .5; pdf.set_font("Sarabun", "B" if bold else "", size)
        pdf.set_x(pdf.l_margin + pad); pdf.cell(tw, h, pdf.fit(text, tw), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(top)
    line("รายงานสรุปผลการดำเนินงานประจำเดือน", 18, True, h=9)
    line(org.get("system_name") or "ระบบปุ่มแจ้งเหตุฉุกเฉินผู้ป่วยติดบ้าน", 13.5)
    for x in (org.get("name"), org.get("partner")):
        if x: line(x, 13.5, h=6.5)
    line(f"เดือน{mth}  ({s['period']['from_be']} – {s['period']['to_be']})", 13.5, True, BLUE)
    pdf.set_y(max(pdf.get_y(), top + 23))
    pdf.set_draw_color(*BLUE); pdf.set_line_width(.6); pdf.line(pdf.l_margin, pdf.get_y() + 2, pdf.w - pdf.r_margin, pdf.get_y() + 2)
    pdf.set_line_width(.2); pdf.ln(5)

    c, r = s["cases"], s["response"]
    pdf.h1("1. สรุปภาพรวม")
    pdf.kpis([("เหตุแจ้งทั้งหมด", f"{c['total']}", f"นอกเวลาราชการ {c['after_hours']}", BLUE),
              ("รับเรื่องเฉลี่ย", _dur(r["accept_avg_s"]), f"มัธยฐาน {_dur(r['accept_median_s'])}", (67, 56, 202)),
              ("รับเรื่องภายในเป้า", _pct(r["within_target_pct"]), f"เป้า {r['target_s']} วินาที", GREEN if (r["within_target_pct"] or 0) >= 90 else AMBER),
              ("ทดสอบปุ่มประจำเดือน", f"{st['tested']}/{st['active']}", f"{_pct(st['pct'])} ของปุ่มที่ใช้งาน", GREEN if st["tested"] == st["active"] else AMBER)])
    rows = [["เหตุแจ้งทั้งหมด (ไม่รวมการทดสอบ)", c["total"], "แก้ไขแล้ว", c["resolved"]],
            ["เหตุผิดพลาด (กดโดยไม่ตั้งใจ)", c["false_alarm"], "ผู้ป่วยยกเลิกเอง", c["cancelled_by_device"]],
            ["เกินเวลารับเรื่อง", c["overdue"], "ต้องแจ้งต่อผู้ดูแลคนถัดไป", c["escalated"]],
            ["ยังเปิดอยู่ ณ สิ้นงวด", c["still_open"], "การกดทดสอบ (แยกจากเหตุจริง)", c["tests"]],
            ["เวลารับเรื่อง 90% ภายใน", _dur(r["accept_p90_s"]), "เวลาปิดเคสเฉลี่ย", f"{r['close_avg_min']:.0f} นาที" if r["close_avg_min"] is not None else "-"],
            ["แจ้ง LINE สำเร็จ", s["line"]["sent"], "แจ้ง LINE ไม่สำเร็จ", s["line"]["failed"]]]
    pdf.table(["รายการ", "จำนวน", "รายการ", "จำนวน"], rows, [62, 27, 62, 27], ["L", "R", "L", "R"])

    pdf.h1("2. ช่วงเวลาที่เกิดเหตุ")
    pdf.set_font("Sarabun", "", 10.5); pdf.set_text_color(*GREY); pdf.cell(0, 5, "จำนวนเหตุแจ้งรายชั่วโมง (เวลาไทย)", new_x="LMARGIN", new_y="NEXT")
    pdf.bars([f"{h:02d}" for h in range(24)], s["by_hour"])
    pdf.table([x["day"] for x in s["by_weekday"]], [[x["cases"] for x in s["by_weekday"]]], [pdf.epw / 7] * 7, ["C"] * 7, zebra=False)

    if s["by_village"]:
        pdf.h1("3. จำแนกตามหมู่บ้าน")
        pdf.table(["หมู่บ้าน", "เหตุแจ้ง", "เหตุผิดพลาด", "รับเรื่องเฉลี่ย"],
                  [[v["village"], v["cases"], v["false_alarm"], _dur(v["accept_avg_s"])] for v in s["by_village"]], [80, 30, 32, 36], ["L", "R", "R", "R"])

    pdf.h1(f"{4 if s['by_village'] else 3}. การทดสอบปุ่มประจำเดือน")
    pdf.set_font("Sarabun", "", 11.5)
    pdf.multi_cell(0, 6.5, align="L", text=f"ปุ่มที่ใช้งาน {st['active']} เครื่อง ทดสอบแล้ว {st['tested']} เครื่อง ({_pct(st['pct'])}) "
                           f"· วิธีทดสอบ: กดปุ่มสั้น ๆ 3 ครั้ง ไฟเขียว = สัญญาณถึงระบบ", new_x="LMARGIN", new_y="NEXT")
    miss = [d for d in st["devices"] if not d["tested"]]
    if miss:
        pdf.ln(1)
        pdf.table(["ยังไม่ได้ทดสอบ", "ผู้ป่วย", "หมู่บ้าน", "อสม. ที่ดูแล", "ทดสอบล่าสุด"],
                  [[d["label"], d["patient"] or "-", d["village"] or "-", d["caregiver"] or "-",
                    reports.be_date(datetime.fromisoformat(d["last_test_at"])) if d["last_test_at"] else "ไม่เคย"] for d in miss],
                  [32, 48, 28, 42, 28], size=10.5, colors=[[RED, None, None, None, None] for _ in miss])

    n = 5 if s["by_village"] else 4
    pdf.h1(f"{n}. อุปกรณ์และเครือข่าย")
    dv = s["devices"]
    pdf.kpis([("ปุ่มที่ใช้งาน", str(dv["active"]), "ณ วันที่พิมพ์", BLUE), ("ออฟไลน์", str(dv["offline_now"]), "ไม่ส่งสัญญาณตามรอบ", RED if dv["offline_now"] else GREEN),
              ("แบตเตอรี่ต่ำ", str(dv["low_battery"]), "ต่ำกว่า 20%", AMBER if dv["low_battery"] else GREEN), ("ไฟบ้านดับ", str(dv["mains_lost"]), "ใช้แบตเตอรี่สำรอง", AMBER if dv["mains_lost"] else GREEN)])
    if s["gateways"]:
        pdf.table(["เกตเวย์", "ขาดการเชื่อมต่อ (ครั้ง)", "รวมเวลาขาด", "ปิดซ่อมบำรุง", "สถานะ"],
                  [[g["label"] or g["gateway_id"], g["outages"], f"{g['downtime_min']:.0f} นาที",
                    f"{g.get('maint_min', 0):.0f} นาที" if g.get("maint_min") else "-", "ยังขาดอยู่" if g["ongoing"] else "กลับมาแล้ว"] for g in s["gateways"]],
                  [58, 34, 30, 30, 26], ["L", "R", "R", "R", "L"])
    else:
        pdf.set_font("Sarabun", "", 11.5); pdf.cell(0, 6.5, "เกตเวย์ทำงานต่อเนื่อง ไม่มีการขาดการเชื่อมต่อในเดือนนี้", new_x="LMARGIN", new_y="NEXT")

    pdf.h1(f"{n + 1}. รายการเหตุแจ้งทั้งหมด ({len(cs)} รายการ)")
    if cs:
        rows, cols = [], []
        for x in cs:
            acc = _dur((_aware(x.accepted_at) - _aware(x.created_at)).total_seconds()) if x.accepted_at else "-"
            res = reports.REASON_TH.get(x.close_reason, "") if x.state == "closed" else reports.STATE_TH.get(x.state, x.state)
            l = _aware(x.created_at).astimezone(reports.tz())
            rows.append([f"#{x.id}", f"{l.day:02d}/{l.month:02d}/{(l.year + 543) % 100:02d} {l:%H:%M}", (x.patient.full_name if names else "") if x.patient else "-",
                         x.patient.village if x.patient else "", res, x.accepted_by or "-", acc])
            cols.append([None, None, None, None, RED if x.state in ("new", "accepted") else (AMBER if x.close_reason == "false_alarm" else GREEN), None, None])
        pdf.table(["เลขเคส", "วันเวลา", "ผู้ป่วย", "หมู่", "ผล", "ผู้รับเรื่อง", "รับเรื่องใน"], rows, [14, 27, 41, 15, 24, 32, 25], size=9.5, colors=cols)
    else:
        pdf.set_font("Sarabun", "", 11.5); pdf.cell(0, 6.5, "ไม่มีเหตุแจ้งในเดือนนี้", new_x="LMARGIN", new_y="NEXT")

    # ---- signatures ----
    if pdf.get_y() > 235: pdf.add_page()
    pdf.ln(12); pdf.set_font("Sarabun", "", 12); pdf.set_text_color(*NAVY)
    col = pdf.epw / 2
    y = pdf.get_y()
    for i, (role_, name_) in enumerate(((org.get("reporter_title") or "ผู้รายงาน", org.get("reporter_name", "")),
                                        (org.get("approver_title") or "ผู้อำนวยการ", org.get("approver_name", "")))):
        x = pdf.l_margin + i * col
        pdf.set_xy(x, y); pdf.cell(col, 7, "ลงชื่อ ........................................................", align="C")
        pdf.set_xy(x, y + 8); pdf.cell(col, 7, f"( {name_ or '........................................................'} )", align="C")
        pdf.set_xy(x, y + 16); pdf.cell(col, 7, pdf.fit(role_, col), align="C")
    return bytes(pdf.output())
