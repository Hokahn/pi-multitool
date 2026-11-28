"""Pi Multitool Gehaeuse V2 (parametrisch, CadQuery).

Aenderungen gegenueber V1
  * echte Masse aus den Fotos (Mic Array mit 4 Bohrungen M3 40 x 40, Sirene 39 x 46,5 x 24,
    UPS HAT ohne Buchstabe (= Bauart A, Buchsenleiste), Klemmen-HAT 64 x 26, KY-040, Taster 12 mm)
  * Seitengriff rechts als eigenes Teil, Sirene und Module im Griff
  * UPS HAT sitzt oben hinten (eigene Stelle, per Kabel an den Klemmen-HAT)
  * Gehaeuse 18 mm duenner (67 statt 84,5 mm)
  * Einschmelzmuttern nach CORGI-Sortiment, 1/4"-Mutter fuers Stativ

Koordinaten: X nach rechts (Sicht des Benutzers von hinten auf das Display),
Y=0 Rueckseite aussen, +Y nach vorne (Richtung Szene/Mikrofone), Z nach oben. Masse in mm.
"""
import json
import math
import os
import sys

import cadquery as cq
from cadquery import Vector as V

HERE = os.path.dirname(os.path.abspath(__file__))
STEP = os.environ.get("MT_STEP", os.path.join(HERE, "..", "STEP")) + os.sep
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "neu")
os.makedirs(OUT, exist_ok=True)

# ------------------------------------------------------------------ Parameter
P = dict(
    W=125.0, H=130.0, D=67.0, T=2.5, R_left=6.0, R_right=2.0, R_front=1.5, clear=0.2,
    # Display / Pi / Klemmen-HAT
    x_off_disp=11.6, z_disp_bottom=13.2, y_glass=1.2,
    standoff_disp_pi=23.0,       # Display-Platine Rueckseite bis Pi-Oberseite (3 mm am Display + 20 mm Bolzen)
    # UPS HAT (A) stehend oben hinten
    ups_x_left=-58.5, ups_z_bottom=71.0, ups_y_cells=3.06,
    # Mikrofon-Array
    mic_cz=52.0, mic_hex_w=78.1, mic_hex_h=88.8, mic_pcb_t=1.6, mic_gap=3.0, mic_r=40.0,
    mic_hole_pitch=40.0, led_r=35.7, led_slot_w=4.8,
    # Kamera
    cam_lens_z=112.0, win_d=12.0, win_t=1.1, win_hole=9.6,
    # Griff
    grip_w=48.0, grip_d=56.0, grip_r_rear=4.0, grip_r_front=12.0,
    # Bedienelemente im Griff
    key_z=82.0, key_dz=-2.15, key_bw=40.0, key_bh=43.0, key_cap_d=36.0, key_hole_d=37.4, key_standoff=6.5,   # Kappe 11 mm ueber Platine
    enc_y=19.0, rec_y=40.0, rec_d=12.3,
    # Sirene (gemessen)
    siren_w=39.0, siren_l=46.5, siren_h=24.0,
    # Einschmelzmuttern (CORGI): Bohrung, Tiefe
    ins_m3=(3.9, 5.5), ins_m3_short=(3.9, 3.6), ins_m2=(3.3, 3.6),
    # 1/4"-20 Sechskantmutter (Schluesselweite 11,1 mm, 5,6 mm dick)
    rn_d=9.3, rn_len=14.5, rn_fl_d=12.6, rn_fl_h=1.6, rn_boss_r=7.5, rn_boss_h=17.0,   # 1/4"-Nietmutter, eingeklebt
)
G = type("G", (), P)
W, H, D, T = G.W, G.H, G.D, G.T
XI = W / 2 - T
ZI0, ZI1 = T, H - T
YF = D - T
XG0, XG1 = W / 2, W / 2 + G.grip_w        # Griff aussen x
XGC = (XG0 + XG1) / 2 - 1.25               # Mitte Griff-Innenraum


# ------------------------------------------------------------------ Helfer
def box(x0, x1, y0, y1, z0, z1):
    return cq.Solid.makeBox(x1 - x0, y1 - y0, z1 - z0, V(x0, y0, z0))


def cyl(p, d, r, h):
    return cq.Solid.makeCylinder(r, h, V(*p), V(*d))


def cyl_y(x, z, y0, y1, r):
    return cq.Solid.makeCylinder(r, y1 - y0, V(x, y0, z), V(0, 1, 0))


def cyl_z(x, y, z0, z1, r):
    return cq.Solid.makeCylinder(r, z1 - z0, V(x, y, z0), V(0, 0, 1))


def cyl_x(y, z, x0, x1, r):
    return cq.Solid.makeCylinder(r, x1 - x0, V(x0, y, z), V(1, 0, 0))


def fuse(base, *others):
    s = base
    for o in others:
        s = s.fuse(o)
    return s.clean()


def cut(base, *others):
    s = base
    for o in others:
        s = s.cut(o)
    return s


def loft(a, b):
    w1 = cq.Wire.makePolygon([V(*p) for p in a], close=True)
    w2 = cq.Wire.makePolygon([V(*p) for p in b], close=True)
    return cq.Solid.makeLoft([w1, w2], True)


def frustum_y(cx, cz, y0, w0, h0, y1, w1, h1):
    r = lambda y, w, h: [(cx - w / 2, y, cz - h / 2), (cx + w / 2, y, cz - h / 2), (cx + w / 2, y, cz + h / 2),
                         (cx - w / 2, y, cz + h / 2)]
    return loft(r(y0, w0, h0), r(y1, w1, h1))


def rrect_y(cx, cz, w, h, r, y0, y1):
    s = cq.Workplane("XZ", origin=(0, y1, 0)).center(cx, cz).rect(w, h).extrude(y1 - y0)
    return (s.edges("|Y").fillet(r) if r > 0 else s).val()


def hex_pts(cx, cz, w, h):
    a, b = w / 2, h / 2
    return [(cx, cz + b), (cx + a, cz + b / 2), (cx + a, cz - b / 2), (cx, cz - b), (cx - a, cz - b / 2),
            (cx - a, cz + b / 2)]


def prism_y(pts, y0, y1):
    return cq.Workplane("XZ", origin=(0, y1, 0)).polyline(pts).close().extrude(y1 - y0).val()


def hexnut_pocket_z(x, y, z0, z1, af):
    r = af / math.sqrt(3)
    return cq.Workplane("XY", origin=(0, 0, z0)).center(x, y).polygon(6, 2 * r).extrude(z1 - z0).val()


def hexnut_pocket_y(x, z, y0, y1, af):
    r = af / math.sqrt(3)
    return cq.Workplane("XZ", origin=(0, y1, 0)).center(x, z).polygon(6, 2 * r).extrude(y1 - y0).val()


def teardrop(center, axis, up, r, length):
    """Loch mit Spitze nach 'oben' (Druckrichtung), damit es ohne Stuetze druckbar ist."""
    c, a, u = V(*center), V(*axis).normalized(), V(*up).normalized()
    s = cq.Solid.makeCylinder(r, length, c - a * (length / 2), a)
    side = a.cross(u).normalized()
    k = r / math.sqrt(2)
    pts0 = [c + side * k + u * k, c + u * (r * math.sqrt(2)), c - side * k + u * k, c]
    w1 = cq.Wire.makePolygon([p - a * (length / 2) for p in pts0], close=True)
    w2 = cq.Wire.makePolygon([p + a * (length / 2) for p in pts0], close=True)
    return s.fuse(cq.Solid.makeLoft([w1, w2], True)).clean()


def xform(shape, rows):
    from OCP.gp import gp_Trsf
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    t = gp_Trsf()
    r = [list(map(float, x)) for x in rows]
    t.SetValues(*r[0], *r[1], *r[2])
    return cq.Shape.cast(BRepBuilderAPI_Transform(shape.wrapped, t, True).Shape())


# ------------------------------------------------------------------ abgeleitete Lagen
z_c_disp = G.z_disp_bottom + 28.0
y_disp_pcb_back = G.y_glass + 7.7
y_pi_top = y_disp_pcb_back + G.standoff_disp_pi
y_pi_bot = y_pi_top + 1.6
x_sd = 43.4 + G.x_off_disp          # SD-Kante des Pi liegt jetzt rechts
z_pi_top, z_pi_bot = z_c_disp + 28.0, z_c_disp - 28.0
disp_rows = [(-1, 0, 0, G.x_off_disp), (0, 0, -1, G.y_glass), (0, -1, 0, z_c_disp)]   # Display 180 Grad gedreht (Flachkabel links)
glass = dict(x0=-44.6 + G.x_off_disp, x1=44.4 + G.x_off_disp, z0=z_c_disp - 26.28, z1=z_c_disp + 26.28)
glass_cx = (glass["x0"] + glass["x1"]) / 2

# Klemmen-HAT (gemessen 64 x 26, Klemmen 8,6 hoch, Buchsenleiste 7,9)
hat_zc = z_pi_bot + 3.5            # GPIO-Leiste jetzt unten
hat_y_board = (y_pi_top - 2.5 - 7.9 - 1.6, y_pi_top - 2.5 - 7.9)
hat_y_term = (hat_y_board[0] - 8.6, hat_y_board[0])

# UPS HAT (A): x_ups -85..0, y_ups 0 (Stiftleiste) .. 56 (Buchsen), z_ups Halter +, Stiftleiste -
ups_X0 = G.ups_x_left + 85.0
ups_Y0 = G.ups_y_cells + 22.84
ups_rows = [(1, 0, 0, ups_X0), (0, 0, -1, ups_Y0), (0, 1, 0, G.ups_z_bottom)]
ups = lambda x, y, z: (x + ups_X0, ups_Y0 - z, G.ups_z_bottom + y)
dc_x = ups(-47.9, 0, 0)[0]; dc_y = ups(0, 0, -3.4)[1]
sw_x0, sw_x1 = ups(-19.5, 0, 0)[0], ups(-10.1, 0, 0)[0]; sw_y = ups(0, 0, -3.6)[1]
ups_holes = [(ups(x, 0, 0)[0], ups(0, y, 0)[2]) for x in (-61.5, -3.5) for y in (3.5, 52.5)]
y_ups_back = ups(0, 0, 1.24)[1]          # Platinenseite mit dem Akkuhalter
ups_hdr = dict(x0=ups(-58.2, 0, 0)[0], x1=ups(-6.9, 0, 0)[0], z0=ups(0, 0.9, 0)[2], z1=ups(0, 6.1, 0)[2],
               y0=ups(0, 0, -2.51)[1], y1=ups(0, 0, -13.51)[1])

# Mikrofon-Array
y_mic_front = YF - G.mic_gap
y_mic_back = y_mic_front - G.mic_pcb_t
mic_holes = [(0.0, G.mic_cz)] + [(G.mic_r * math.sin(math.radians(a)), G.mic_cz + G.mic_r * math.cos(math.radians(a)))
                                 for a in range(0, 360, 60)]
hp = G.mic_hole_pitch / 2
mic_mount = [(sx * hp, G.mic_cz + sz * hp) for sx in (-1, 1) for sz in (-1, 1)]

# Kamera
cam_Xc, cam_Zc = 12.5, G.cam_lens_z - 14.4
cam_Yc = YF - 7.65                 # Objektivspitze buendig mit Innenseite Frontwand
cam_rows = [(0, -1, 0, cam_Xc), (0, 0, -1, cam_Yc), (1, 0, 0, cam_Zc)]
cam_holes = [(cam_Xc - yc, cam_Zc + xc) for xc in (2.0, 14.5) for yc in (2.0, 23.0)]   # aus STEP: Bohrungen auf Hoehe Objektiv und 2 mm vom unteren Rand
y_cam_front = cam_Yc + 0.75

# Gehaeuse-Verschraubung, Displayhalter, Stative
rear_screws = [(-56.5, 70.0), (-56.5, 124.0), (56.5, 124.0), (56.5, 6.0)]
disp_clamps = [(0.0, z_pi_top + 2.2, 'z'), (glass["x0"] - 2.5, z_c_disp, 'x')]   # Lasche oben und links
tripod_bottom = (0.0, 48.0)        # x, y
tripod_rear = (-45.0, 64.5)         # x, z
rp_x0, rp_y0 = -36.0, 62.0          # RP2040 liegt flach, USB zeigt nach hinten
rp_rows = [(-1, 0, 0, rp_x0), (0, -1, 0, rp_y0), (0, 0, 1, 4.9 + 1.09)]

# Griff
GY = G.grip_d
grip_screws = [(6.0, 35.0), (6.0, 113.0), (48.0, 35.0), (48.0, 105.0)]   # (y, z)
side_windows = [(8.0, 44.0), (50.0, 86.0), (92.0, 124.0)]                  # z-Bereiche, y 10..44
enc_x = XGC
enc_rows = [(1, 0, 0, enc_x), (0, 1, 0, G.enc_y), (0, 0, 1, ZI1 - 4.65)]
rec_x = XGC
key_x = XGC
key_clamps = []
key_bz = G.key_z + G.key_dz          # Platinenmitte (Kappe sitzt 2,15 mm ueber der Platinenmitte)
key_hooks = [(key_x + sx * 12.0, sz) for sx in (-1, 1) for sz in (-1, 1)]   # Schnapphaken oben/unten
siren_x0 = XGC - G.siren_w / 2
siren_y0 = (GY - G.siren_l) / 2
grip_floor = (3.0, 5.5)
siren_z0 = grip_floor[1]
siren_holes = [(siren_x0 + 4.4, siren_y0 + 4.4), (siren_x0 + G.siren_w - 4.4, siren_y0 + G.siren_l - 4.4)]
siren_membrane = (siren_x0 + 19.5, siren_y0 + 18.0)
carrier_bosses = [(30.0, 50.0), (30.0, 96.0)]     # (y, z) an der Griff-Aussenwand
x_carrier = XG1 - T - 3.0
mod_y0 = 14.0                        # Innenseite Traegerplatte


# ------------------------------------------------------------------ Gehaeuse (Frontschale)
def front_shell():
    outer = cq.Workplane().box(W, D, H, centered=(True, False, False))
    outer = outer.edges("|Y and <X").fillet(G.R_left).edges("|Y and >X").fillet(G.R_right)
    outer = outer.faces(">Y").edges().fillet(G.R_front).val()
    inner = (cq.Workplane().box(2 * XI, YF, ZI1 - ZI0, centered=(True, False, False))
             .translate((0, 0, ZI0)).edges("|Y").fillet(1.5)).val()
    s = outer.cut(inner)

    add = []
    # Schraubdome Rueckdeckel (kurz) + Fuellung zur Wand + Druckkeil
    for x, z in rear_screws:
        sx = 1 if x > 0 else -1
        add.append(cyl_y(x, z, T, 7.5, 4.0))
        add.append(box(min(x, sx * XI), max(x, sx * XI), T, 7.5, z - 4, z + 4))
        a0, a1 = sorted((sx * XI, x - sx * 4.0)); w0, w1 = sorted((sx * XI, sx * (XI - 0.6)))
        add.append(loft([(a0, 7.5, z - 4), (a1, 7.5, z - 4), (a1, 7.5, z + 4), (a0, 7.5, z + 4)],
                        [(w0, 11.5, z - 4), (w1, 11.5, z - 4), (w1, 11.5, z + 4), (w0, 11.5, z + 4)]))
        if z > 100 or z < 10:
            zw = ZI1 if z > 60 else ZI0
            add.append(box(x - 4, x + 4, T, 7.5, min(z, zw), max(z, zw)))
    # Auflageleisten fuer den Rueckdeckel
    add += [box(-XI, -XI + 1.5, T, T + 1.5, 47, 57), box(XI - 1.5, XI, T, T + 1.5, 64, 74),
            box(-10, 5, T, T + 1.5, ZI1 - 1.5, ZI1), box(-50, -35, T, T + 1.5, ZI0, ZI0 + 1.5)]
    # Mic Array: 4 Abstandsdome M3, Dichtroehrchen
    for x, z in mic_mount:
        add.append(cyl_y(x, z, y_mic_front, YF, 3.4))
    for x, z in mic_holes:
        add.append(cyl_y(x, z, y_mic_front + 0.2, YF, 1.5))
    # Kamera-Dome
    for x, z in cam_holes:
        add.append(cyl_y(x, z, y_cam_front, YF, 2.7))
    # Stativ unten (1/4"-Mutter) + Druckkeil
    tx, ty = tripod_bottom
    rb, rh = G.rn_boss_r, G.rn_boss_h
    add.append(cyl_z(tx, ty, ZI0 - 0.5, rh, rb))
    add.append(loft([(tx - rb, ty, ZI0), (tx + rb, ty, ZI0), (tx + rb, ty, rh), (tx - rb, ty, rh)],
                    [(tx - rb, ty + rh, ZI0), (tx + rb, ty + rh, ZI0), (tx + rb, ty + rh, ZI0 + 0.6), (tx - rb, ty + rh, ZI0 + 0.6)]))
    # RP2040-Halter (Board liegt flach auf zwei Leisten)
    add += [box(rp_x0 - 20.5, rp_x0 - 18.25, rp_y0 - 25.0, rp_y0 + 0.5, ZI0 - 0.1, 8.0),
            box(rp_x0 + 0.25, rp_x0 + 2.5, rp_y0 - 25.0, rp_y0 + 0.5, ZI0 - 0.1, 8.0),
            box(rp_x0 - 18.25, rp_x0 - 16.5, rp_y0 - 25.0, rp_y0 + 0.5, ZI0 - 0.1, 4.9),
            box(rp_x0 - 1.5, rp_x0 + 0.25, rp_y0 - 25.0, rp_y0 + 0.5, ZI0 - 0.1, 4.9),
            box(rp_x0 - 20.5, rp_x0 + 2.5, rp_y0 + 0.5, rp_y0 + 2.5, ZI0 - 0.1, 8.0)]
    s = fuse(s, *add)

    cuts = []
    for x, z in rear_screws:
        cuts.append(cyl_y(x, z, T - 0.1, T + G.ins_m3_short[1], G.ins_m3_short[0] / 2))
    for x, z in mic_mount:
        cuts.append(cyl_y(x, z, y_mic_front - 0.1, y_mic_front + 4.6, G.ins_m3_short[0] / 2))
    for x, z in mic_holes:
        cuts.append(cyl_y(x, z, y_mic_front, D + 1, 1.0))
    # LED-Schlitz mit 6 Stegen (zwischen den LEDs) und Vertiefung fuer den Diffusorflansch
    r1, r2 = G.led_r - G.led_slot_w / 2, G.led_r + G.led_slot_w / 2
    ring = cq.Solid.makeCylinder(r2, T + 2, V(0, YF - 1, G.mic_cz), V(0, 1, 0)).cut(
        cq.Solid.makeCylinder(r1, T + 2, V(0, YF - 1, G.mic_cz), V(0, 1, 0)))
    for a in range(15, 360, 60):
        br = box(-1.25, 1.25, YF - 2, D + 2, G.mic_cz, G.mic_cz + 50).rotate(V(0, 0, G.mic_cz), V(0, 1, G.mic_cz), -a)
        ring = ring.cut(br)
    cuts.append(ring)
    cuts.append(cq.Solid.makeCylinder(r2 + 0.6, 1.8, V(0, D - 0.8, G.mic_cz), V(0, 1, 0)).cut(
        cq.Solid.makeCylinder(r1 - 0.6, 3, V(0, D - 1, G.mic_cz), V(0, 1, 0))))
    # Kamera
    # Schutzscheibe 12 x 1 mm (PC, gelasert) in Vertiefung, darunter Auflagering
    cuts.append(cyl_y(0, G.cam_lens_z, YF - 0.1, D - G.win_t, G.win_hole / 2))
    cuts.append(cyl_y(0, G.cam_lens_z, D - G.win_t, D + 1, G.win_d / 2 + 0.2))
    cuts.append(box(-0.75, 0.75, D - G.win_t, D + 1, G.cam_lens_z - G.win_d / 2 - 1.2, G.cam_lens_z))   # Kerbe zum Heraushebeln
    for x, z in cam_holes:
        cuts.append(cyl_y(x, z, y_cam_front - 0.1, y_cam_front + G.ins_m2[1], G.ins_m2[0] / 2))
    # Oben: UPS-Ladebuchse und Schalter, Lueftung
    cuts.append(cyl_z(dc_x, dc_y, ZI1 - 1, H + 1, 5.3))
    cuts.append(box(sw_x0, sw_x1, sw_y - 2.6, sw_y + 2.6, ZI1 - 1, H + 1))
    cuts.append(box(sw_x0 - 3, sw_x1 + 3, sw_y - 5, sw_y + 5, H - 1.0, H + 1))
    for i in range(5):
        x = -55.0 + i * 6.0
        cuts.append(box(x, x + 2.2, 37.0, 52.0, ZI1 - 1, H + 1))
    # Unten: Stativmutter, Lueftung unter dem Pi
    tx, ty = tripod_bottom
    cuts.append(cyl_z(tx, ty, -1, G.rn_len, G.rn_d / 2))
    cuts.append(cyl_z(tx, ty, -1, G.rn_fl_h, G.rn_fl_d / 2))
    for i in range(7):
        x = -50.0 + i * 9.0
        cuts.append(box(x, x + 2.4, 13.0, 30.0, -1, ZI0 + 1))
    # Links: Lueftung beim Pi, Vertiefung fuer TPU-Polster
    for i in range(5):
        y = 14.0 + i * 4.5
        cuts.append(box(-W / 2 - 1, -XI + 1, y, y + 2.2, 15.0, 50.0))
    pad = (cq.Workplane("YZ", origin=(-W / 2 - 1, 0, 0)).center(33, 90).rect(44, 56).extrude(1 + 1.2)
           .edges("|X").fillet(6)).val()
    cuts.append(pad)
    # Rechts: Fenster zum Griff + Schraubloecher
    for z0, z1 in side_windows:
        cuts.append(box(XI - 1, W / 2 + 1, 10.0, 44.0, z0, z1))
    for y, z in grip_screws:
        cuts.append(cyl_x(y, z, XI - 1, W / 2 + 1, 1.7))
    return cut(s, *cuts)


# ------------------------------------------------------------------ Griff
def grip_profile(x0, x1, y0, y1, r_rear, r_front):
    w = (cq.Workplane("XY").moveTo(x0, y0).lineTo(x1 - r_rear, y0)
         .threePointArc((x1 - r_rear + r_rear * math.sin(math.pi / 4), y0 + r_rear - r_rear * math.cos(math.pi / 4)), (x1, y0 + r_rear))
         .lineTo(x1, y1 - r_front)
         .threePointArc((x1 - r_front + r_front * math.cos(math.pi / 4), y1 - r_front + r_front * math.sin(math.pi / 4)), (x1 - r_front, y1))
         .lineTo(x0, y1).close())
    return w


def grip():
    outer = grip_profile(XG0, XG1, 0, GY, G.grip_r_rear, G.grip_r_front).extrude(H)
    outer = outer.edges(cq.selectors.BoxSelector((XG0 + 0.5, -1, H - 0.5), (XG1 + 1, GY + 1, H + 0.5))).fillet(4)
    outer = outer.edges(cq.selectors.BoxSelector((XG0 + 0.5, -1, -0.5), (XG1 + 1, GY + 1, 0.5))).fillet(3)
    outer = outer.val()
    inner = grip_profile(XG0 - 3, XG1 - T, T, GY - T, G.grip_r_rear - T, G.grip_r_front - T).extrude(ZI1 - grip_floor[1]) \
        .translate((0, 0, grip_floor[1])).val()
    s = outer.cut(inner)
    # Bodenmulde: Rand unten 3 mm, Boden 3..5,5
    s = s.cut(grip_profile(XG0 + T, XG1 - T, T, GY - T, G.grip_r_rear - T, G.grip_r_front - T).extrude(grip_floor[0] + 0.01)
              .translate((0, 0, -0.01)).val())
    add = []
    # Schraubdome zum Gehaeuse (M3-Einschmelzmutter), Druckkeile zur Aussenwand hin
    for y, z in grip_screws:
        add.append(cyl_x(y, z, XG0, XG0 + 9.0, 4.0))
        yw = 0.0 if y < GY / 2 else GY
        add.append(box(XG0, XG0 + 9.0, min(y, yw + (T if yw == 0 else -T)), max(y, yw + (T if yw == 0 else -T)), z - 4, z + 4))
        add.append(loft([(XG0 + 9.0, y - 4, z - 4), (XG0 + 9.0, y + 4, z - 4), (XG0 + 9.0, y + 4, z + 4), (XG0 + 9.0, y - 4, z + 4)],
                        [(XG0 + 17.0, y - 4, z - 0.3), (XG0 + 17.0, y + 4, z - 0.3), (XG0 + 17.0, y + 4, z + 0.3), (XG0 + 17.0, y - 4, z + 0.3)]))
    # Tastenkreuz: 4 Saeulen an der Rueckwand
    bw, bh, yb = G.key_bw / 2 + 0.3, G.key_bh / 2 + 0.3, T + G.key_standoff
    for sx in (-1, 1):
        for sz in (-1, 1):
            cx, cz = key_x + sx * bw, key_bz + sz * bh
            add.append(box(min(cx, cx - sx * 6), max(cx, cx - sx * 6), T - 0.1, yb, min(cz, cz - sz * 2.5), max(cz, cz - sz * 2.5)))
            add.append(box(min(cx, cx - sx * 2.5), max(cx, cx - sx * 2.5), T - 0.1, yb, min(cz, cz - sz * 6), max(cz, cz - sz * 6)))
            add.append(box(min(cx, cx + sx * 1.2), max(cx, cx + sx * 1.2), T - 0.1, yb + 1.6, min(cz, cz - sz * 6), max(cz, cz - sz * 6)))
            add.append(box(min(cx, cx - sx * 6), max(cx, cx - sx * 6), T - 0.1, yb + 1.6, min(cz, cz + sz * 1.2), max(cz, cz + sz * 1.2)))
    # Schnapphaken: Platine von innen gegen die Rueckwand druecken, bis sie einrastet
    for x, sz in key_hooks:
        ze = key_bz + sz * bh                                  # Innenkante Haken = Platinenkante + Spiel
        z0, z1 = sorted((ze, ze + sz * 1.5))
        add.append(box(x - 2.5, x + 2.5, T - 0.1, yb + 1.6 + 2.2, z0, z1))
        cz0 = ze; cz1 = ze - sz * 0.75                         # Rastnase ragt 0,75 mm ueber die Platine
        add.append(loft([(x - 2.5, yb + 1.65, cz0), (x + 2.5, yb + 1.65, cz0), (x + 2.5, yb + 1.65, cz1), (x - 2.5, yb + 1.65, cz1)],
                        [(x - 2.5, yb + 1.6 + 2.2, cz0), (x + 2.5, yb + 1.6 + 2.2, cz0), (x + 2.5, yb + 1.6 + 2.2, cz0 - sz * 0.05), (x - 2.5, yb + 1.6 + 2.2, cz0 - sz * 0.05)]))
        add.append(box(x - 3.5, x + 3.5, T - 0.1, T + 1.0, min(z0, z1) - 1.0 if sz > 0 else min(z0, z1), max(z0, z1) if sz > 0 else max(z0, z1) + 1.0))
    # Modultraeger: 2 Dome an der Aussenwand
    for y, z in carrier_bosses:
        add.append(cyl_x(y, z, x_carrier, XG1 - T + 0.1, 4.0))
    s = fuse(s, *add)

    cuts = []
    for y, z in grip_screws:
        cuts.append(cyl_x(y, z, XG0 - 0.1, XG0 + G.ins_m3[1], G.ins_m3[0] / 2))
    up = (-1, 0, 0)     # Druckrichtung (Griff liegt mit der Aussenseite auf dem Bett)
    cuts.append(teardrop((rec_x, G.rec_y, H - 1.25), (0, 0, 1), up, G.rec_d / 2, 6))
    cuts.append(teardrop((enc_x, G.enc_y, H - 1.25), (0, 0, 1), up, 3.65, 6))
    cuts.append(cyl_y(key_x, G.key_z, -1, T + 1, G.key_hole_d / 2))
    # Sirenengitter im Boden + Schallschlitze im Rand
    mx, my = siren_membrane
    for i in range(-4, 5):
        for j in range(-4, 5):
            x = mx + i * 4.0 + (2.0 if j % 2 else 0); y = my + j * 3.46
            if (x - mx) ** 2 + (y - my) ** 2 < 11.5 ** 2:
                cuts.append(cyl_z(x, y, grip_floor[0] - 0.5, grip_floor[1] + 0.5, 1.2))
    for x, y in siren_holes:
        cuts.append(cyl_z(x, y, grip_floor[0] - 0.5, grip_floor[1] + 0.1, 1.7))
    cuts.append(box(XG0 + 6, XG0 + 20, GY - T - 1, GY + 1, -1, grip_floor[0]))
    cuts.append(box(XG1 - T - 1, XG1 + 1, 18, 34, -1, grip_floor[0]))
    cuts.append(box(XG0 + 10, XG0 + 26, -1, T + 1, -1, grip_floor[0]))
    for y, z in carrier_bosses:
        cuts.append(cyl_x(y, z, x_carrier - 0.1, x_carrier + 6, 1.25))
    # Vertiefung fuer TPU-Polster vorne
    cuts.append((cq.Workplane("XZ", origin=(0, GY + 1, 0)).center(XG0 + 16.5, 72).rect(28, 80).extrude(1 + 1.2)
                 .edges("|Y").fillet(5)).val())
    return cut(s, *cuts)


# ------------------------------------------------------------------ Rueckdeckel
def rear_cover():
    c = G.clear
    outline = lambda y0, y1: rrect_y(0, H / 2, 2 * XI - 2 * c, ZI1 - ZI0 - 2 * c, 1.5 - c, y0, y1)
    plate = outline(0, T)
    add = []
    for x, z, _ in disp_clamps:
        add.append(cyl_y(x, z, T, y_disp_pcb_back, 2.0))
    # Haken rechts (haelt die Display-Platine an der rechten Kante)
    add.append(box(glass["x1"] + 0.4, XI - G.clear - 0.2, T, y_disp_pcb_back + 1.5, 20, 45))
    add.append(box(x_sd - 1.3, XI - G.clear - 0.2, y_disp_pcb_back + 0.05, y_disp_pcb_back + 1.5, 20, 45))
    # Haken unten fuer die Display-Platine
    zb = G.z_disp_bottom
    for xa, xb in ((-25.0, -12.0), (28.0, 42.0)):
        add.append(box(xa, xb, T, y_disp_pcb_back + 1.5, ZI0 + c, zb - 0.2))
        add.append(box(xa, xb, y_disp_pcb_back + 0.05, y_disp_pcb_back + 1.5, ZI0 + c, zb + 1.3))
    # UPS HAT: 4 Saeulen M2,5 vom Deckel bis zur Platine, mit Stuetzrippen
    for x, z in ups_holes:
        add.append(cyl_y(x, z, T - 0.1, y_ups_back, 3.0))
        for sx in (-1, 1):                                    # Rippen waagrecht links und rechts
            add.append(loft([(x, T, z - 0.9), (x + sx * 9.0, T, z - 0.9), (x + sx * 9.0, T, z + 0.9), (x, T, z + 0.9)],
                            [(x, y_ups_back - 4, z - 0.9), (x + sx * 2.5, y_ups_back - 4, z - 0.9), (x + sx * 2.5, y_ups_back - 4, z + 0.9), (x, y_ups_back - 4, z + 0.9)]))
    # Stativ hinten
    tx, tz = tripod_rear
    add.append(cyl_y(tx, tz, T, G.rn_boss_h, G.rn_boss_r))
    s = fuse(plate, *add).intersect(outline(-1, 30))
    cuts = []
    gw, gh = glass["x1"] - glass["x0"] + 0.4, glass["z1"] - glass["z0"] + 0.4
    cuts.append(box(glass_cx - gw / 2, glass_cx + gw / 2, G.y_glass, T + 1, z_c_disp - gh / 2, z_c_disp + gh / 2))
    cuts.append(frustum_y(glass_cx, z_c_disp, G.y_glass + 0.01, 76.8, 46.4, -0.01, 79.2, 48.8))
    for x, z in rear_screws:
        cuts.append(cyl_y(x, z, -1, T + 1, 1.7))
        cuts.append(cq.Solid.makeCone(3.2, 1.6, 1.6, V(x, -0.01, z), V(0, 1, 0)))
    for x, z, _ in disp_clamps:
        cuts.append(cyl_y(x, z, T + 0.5, y_disp_pcb_back + 1, 1.0))
    for x, z in ups_holes:
        cuts.append(cyl_y(x, z, y_ups_back - 8.0, y_ups_back + 0.1, 1.05))
    cuts.append(cyl_y(tx, tz, -1, G.rn_len, G.rn_d / 2))
    cuts.append(cyl_y(tx, tz, -1, G.rn_fl_h, G.rn_fl_d / 2))
    # Fenster fuer die Lade-LEDs des UPS HAT ist nicht noetig (Ladeanzeige ueber Software)
    return cut(s, *cuts)


# ------------------------------------------------------------------ Kleinteile
def diffuser():
    r1, r2 = G.led_r - G.led_slot_w / 2 + 0.15, G.led_r + G.led_slot_w / 2 - 0.15
    body = cq.Solid.makeCylinder(r2, T - 0.3, V(0, 0, 0), V(0, 0, 1)).cut(
        cq.Solid.makeCylinder(r1, T + 2, V(0, 0, -0.5), V(0, 0, 1)))
    for a in range(15, 360, 60):
        body = body.cut(box(-1.45, 1.45, 0, 50, -1, T + 1).rotate(V(0, 0, 0), V(0, 0, 1), -a))
    fl = cq.Solid.makeCylinder(r2 + 0.75, 0.8, V(0, 0, T - 0.3), V(0, 0, 1)).cut(
        cq.Solid.makeCylinder(r1 - 0.75, 2, V(0, 0, T - 0.5), V(0, 0, 1)))
    return body.fuse(fl).clean()


def clamp_tab():
    return (cq.Workplane().box(7, 12, 2, centered=(True, False, False)).edges("|Z").fillet(1.5)
            .faces(">Z").workplane().center(0, 4).hole(2.8)).val()


def carrier():
    p = cq.Workplane("YZ").rect(36, 64).extrude(2.5).edges("|X").fillet(3)
    zc = (carrier_bosses[0][1] + carrier_bosses[1][1]) / 2
    p = p.faces(">X").workplane().pushPoints([(0, z - zc) for _, z in carrier_bosses]).hole(3.3)
    pts = [(y, z) for y in range(-15, 16, 5) for z in range(-30, 31, 5) if abs(z - (carrier_bosses[0][1] - zc)) > 4 and abs(z - (carrier_bosses[1][1] - zc)) > 4]
    p = p.faces(">X").workplane().pushPoints(pts).hole(2.2)
    return p.val(), zc


def pad(w, h, r, t=1.4):
    return cq.Workplane("XY").rect(w - 0.4, h - 0.4).extrude(t).edges("|Z").fillet(r - 0.2).val()


# ------------------------------------------------------------------ Bauteile (Referenz)
def comps():
    C = {}
    C["Display"] = xform(cq.importers.importStep(STEP + "Display_3.5inch_DSI_LCD_H.stp").val(), disp_rows)
    C["UPS_HAT_A"] = xform(cq.importers.importStep(STEP + "UPS_HAT_A.stp").val(), ups_rows)
    C["Kamera"] = xform(cq.importers.importStep(STEP + "Camera_Module_3_Standard.stp").val(), cam_rows)
    enc = xform(cq.importers.importStep(STEP + "Drehencoder_EC11_generisch.step").val(), enc_rows)
    zb = ZI1 - 4.65
    C["Encoder_KY040"] = fuse(enc, box(enc_x - 14.6, enc_x + 11.4, G.enc_y - 9.5, G.enc_y + 9.5, zb - 1.6, zb),
                              box(enc_x - 14.6 - 8.0, enc_x - 14.6, G.enc_y - 6.5, G.enc_y + 6.5, zb - 2.6, zb - 0.1))
    C["Dupont_Encoder"] = box(enc_x - 14.6 - 8.0 - 14.0, enc_x - 14.6 - 8.0, G.enc_y - 6.5, G.enc_y + 6.5, zb - 3.0, zb + 0.0)
    C["RP2040"] = xform(cq.importers.importStep(STEP + "RP2040_Zero.stp").val(), rp_rows)
    C["Dupont_RP2040"] = fuse(box(rp_x0 - 17.5, rp_x0 - 15.0, rp_y0 - 23.0, rp_y0 - 1.0, 6.0, 27.0),
                              box(rp_x0 - 3.0, rp_x0 - 0.5, rp_y0 - 23.0, rp_y0 - 1.0, 6.0, 27.0))

    def pi(xp0, xp1, yp0, yp1, h0, h1):
        return box(x_sd - xp1, x_sd - xp0, y_pi_top - h1, y_pi_top - h0, z_pi_bot + yp0, z_pi_bot + yp1)
    C["Pi3B"] = fuse(pi(0, 85, 0, 56, -1.6, 0), pi(7.1, 57.9, 1, 6, 0, 8.5), pi(66, 87, 2, 54, 0, 16),
                     pi(24.5, 39.5, 45, 56.5, 0, 6.5), pi(50.5, 56.5, 41, 57, 0, 6), pi(-2.5, 12, 22, 34, -3.0, -1.6))
    C["Klemmen_HAT"] = fuse(
        box(x_sd - 64.5, x_sd - 0.5, hat_y_board[0], hat_y_board[1], hat_zc - 13, hat_zc + 13),
        box(x_sd - 63.5, x_sd - 1.5, hat_y_term[0], hat_y_term[1], hat_zc + 6.0, hat_zc + 13.5),
        box(x_sd - 63.5, x_sd - 1.5, hat_y_term[0], hat_y_term[1], hat_zc - 13.5, hat_zc - 6.0),
        box(x_sd - 57.9, x_sd - 7.1, hat_y_board[1], y_pi_top - 2.5, hat_zc - 2.6, hat_zc + 2.6))
    C["Abstandshalter"] = fuse(*[cyl_y(x, z, G.y_glass + 10.7, y_pi_top, 2.5)
                                 for x in (x_sd - 3.5, x_sd - 61.5) for z in (z_pi_top - 3.5, z_pi_bot + 3.5)])
    C["Dupont_UPS"] = box(ups_hdr["x0"], ups_hdr["x0"] + 16.0, ups_hdr["y1"], ups_hdr["y1"] + 14.0, ups_hdr["z0"], ups_hdr["z1"])
    hexp = hex_pts(0, G.mic_cz, G.mic_hex_w, G.mic_hex_h)
    mic = prism_y(hexp, y_mic_back, y_mic_front)
    for a in range(0, 360, 30):
        x = G.led_r * math.sin(math.radians(a)); z = G.mic_cz + G.led_r * math.cos(math.radians(a))
        mic = mic.fuse(box(x - 2.5, x + 2.5, y_mic_front, y_mic_front + 1.6, z - 2.5, z + 2.5).rotate(V(x, 0, z), V(x, 1, z), a))
    C["Mic_Array"] = mic.clean()
    C["Mic_Stecker"] = fuse(box(-10.2, 10.2, y_mic_back - 9.0, y_mic_back, G.mic_cz - 4.5, G.mic_cz + 4.5),
                            box(-6.6, 6.6, y_mic_back - 23.0, y_mic_back - 9.0, G.mic_cz - 2.8, G.mic_cz + 2.8))
    ks = T + G.key_standoff
    C["5_Wege_Taste"] = fuse(box(key_x - G.key_bw / 2, key_x + G.key_bw / 2, ks, ks + 1.6, key_bz - G.key_bh / 2, key_bz + G.key_bh / 2),
                             cyl_y(key_x, G.key_z, ks - 11.0, ks - 4.0, G.key_cap_d / 2),
                             box(key_x - 19.25, key_x + 19.25, ks - 4.0, ks, G.key_z - 6.25, G.key_z + 6.25),
                             box(key_x - 6.25, key_x + 6.25, ks - 4.0, ks, G.key_z - 19.25, G.key_z + 19.25),
                             box(key_x - 8, key_x + 8, ks + 1.6, ks + 3.0, key_bz - G.key_bh / 2 + 1, key_bz - G.key_bh / 2 + 4))
    C["Sirene"] = (cq.Workplane("XY", origin=(0, 0, siren_z0)).center(siren_x0 + G.siren_w / 2, siren_y0 + G.siren_l / 2)
                   .rect(G.siren_w, G.siren_l).extrude(G.siren_h).edges("|Z").fillet(3)).val()
    C["Aufnahmetaste"] = fuse(cyl_z(rec_x, G.rec_y, ZI1 - 18, H, 6.0), cyl_z(rec_x, G.rec_y, H, H + 3, 7.0),
                              cyl_z(rec_x, G.rec_y, ZI1 - 2.2, ZI1, 7.5))
    xm = x_carrier - 2.5
    C["MOSFET_Modul"] = box(xm - 13, xm, 15, 48, 82, 103)
    C["MT3608"] = box(xm - 6, xm, 14, 50, 61, 78)
    C["MP1584"] = box(xm - 4, xm, 14, 36, 42, 59)
    C["RTC_DS3231"] = box(xm - 11, xm, 37, 51, 42, 57)
    return C


# ------------------------------------------------------------------ Hauptprogramm
if __name__ == "__main__":
    car, car_zc = carrier()
    parts = {
        "Frontschale": front_shell(),
        "Griff": grip(),
        "Rueckdeckel": rear_cover(),
        "Diffusorring": diffuser(),
        "Klemmlasche": clamp_tab(),
        "Modultraeger": car,
        "Griffpolster_vorne_TPU": pad(28, 80, 5),
        "Griffpolster_links_TPU": pad(56, 44, 6),
    }
    for n, p in parts.items():
        print(n, "valid", p.isValid(), "vol %.0f" % p.Volume(), flush=True)
        cq.exporters.export(p, f"{OUT}/{n}.step")
        cq.exporters.export(p, f"{OUT}/{n}.stl", tolerance=0.05, angularTolerance=0.15)
    C = comps()
    tab = parts["Klemmlasche"]
    placed = {}
    for i, (x, z, d) in enumerate(disp_clamps):
        t = tab.rotate(V(0, 0, 0), V(1, 0, 0), -90)         # Lasche zeigt nach -z, Loch bei z=-4
        if d == 'x':
            t = t.rotate(V(0, 0, 0), V(0, 1, 0), -90)       # Lasche zeigt nach +x (zum Display)
        bb = t.BoundingBox()
        if d == 'z':
            t = t.translate(V(x, y_disp_pcb_back - bb.ymin, z + 4))
        else:
            t = t.translate(V(x - 4, y_disp_pcb_back - bb.ymin, z))
        placed[f"Klemmlasche_{i+1}"] = t
    for i, (x, z, sz) in enumerate(key_clamps):
        t = tab.rotate(V(0, 0, 0), V(1, 0, 0), -90 if sz < 0 else 90)
        bb = t.BoundingBox()
        t = t.translate(V(x, T + G.key_standoff + 1.6 - bb.ymin, z + (4 if sz < 0 else -4)))
        placed[f"Klemmlasche_T{i+1}"] = t
    for rot in (0, 30):
        dif = parts["Diffusorring"].rotate(V(0, 0, 0), V(0, 0, 1), rot).rotate(V(0, 0, 0), V(1, 0, 0), -90) \
            .translate(V(0, YF + 0.3, G.mic_cz))
        if dif.intersect(parts["Frontschale"]).Volume() < 0.5:
            break
    print("Diffusor gedreht um", rot)
    placed["Diffusor"] = dif
    placed["Modultraeger"] = car.translate(V(x_carrier - 2.5, carrier_bosses[0][0], car_zc))
    placed["Polster_vorne"] = parts["Griffpolster_vorne_TPU"].rotate(V(0, 0, 0), V(1, 0, 0), -90).translate(V(XG0 + 16.5, GY - 1.2, 72))
    placed["Polster_links"] = parts["Griffpolster_links_TPU"].rotate(V(0, 0, 0), V(0, 1, 0), -90).translate(V(-W / 2 + 1.2, 33, 90))

    asm = cq.Assembly(name="Pi_Multitool_V2")
    asm.add(parts["Frontschale"], name="Frontschale", color=cq.Color(0.25, 0.27, 0.30, 1))
    asm.add(parts["Griff"], name="Griff", color=cq.Color(0.25, 0.27, 0.30, 1))
    asm.add(parts["Rueckdeckel"], name="Rueckdeckel", color=cq.Color(0.32, 0.34, 0.38, 1))
    for n, t in placed.items():
        asm.add(t, name=n, color=cq.Color(0.9, 0.5, 0.1, 1))
    for n, s in C.items():
        asm.add(s, name=n, color=cq.Color(0.2, 0.5, 0.3, 1))
    asm.save(f"{OUT}/Baugruppe.step")

    printed = {"Frontschale": parts["Frontschale"], "Griff": parts["Griff"], "Rueckdeckel": parts["Rueckdeckel"], **placed}
    report = []
    for pn, ps in printed.items():
        pb = ps.BoundingBox()
        for cn, cs in C.items():
            cb = cs.BoundingBox()
            if pb.xmax < cb.xmin or cb.xmax < pb.xmin or pb.ymax < cb.ymin or cb.ymax < pb.ymin or pb.zmax < cb.zmin or cb.zmax < pb.zmin:
                continue
            v = 0.0
            for sol in cs.Solids():
                sb = sol.BoundingBox()
                if pb.xmax < sb.xmin or sb.xmax < pb.xmin or pb.ymax < sb.ymin or sb.ymax < pb.ymin or pb.zmax < sb.zmin or sb.zmax < pb.zmin:
                    continue
                try:
                    v += ps.intersect(sol).Volume()
                except Exception:
                    pass
            if v > 0.5:
                report.append((pn, cn, round(v, 1)))
    print("KOLLISIONEN:", report, flush=True)
    pn = list(printed)
    for i in range(len(pn)):
        for j in range(i + 1, len(pn)):
            try:
                v = printed[pn[i]].intersect(printed[pn[j]]).Volume()
            except Exception:
                v = -1
            if v > 0.5:
                print("Teile-Ueberlappung", pn[i], pn[j], round(v, 1))
    names = list(C)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            try:
                v = C[names[i]].intersect(C[names[j]]).Volume()
            except Exception:
                v = -1
            if v > 0.5:
                print("Bauteil-Ueberlappung", names[i], names[j], round(v, 1))
    os.makedirs(f"{OUT}/mesh", exist_ok=True)
    for n, s in {**printed, **C}.items():
        cq.exporters.export(s, f"{OUT}/mesh/{n}.stl", tolerance=0.1, angularTolerance=0.3)
    json.dump({"params": P}, open(f"{OUT}/parameter.json", "w"), indent=1)
    print("fertig")
