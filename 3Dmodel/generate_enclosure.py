"""
ESP32AirCheck enclosure generator (Blender 5.x, headless).

Run:
  "C:\\Program Files\\Blender Foundation\\Blender 5.x\\blender.exe" -b --python generate_enclosure.py

All dimensions are millimetres.  The PCB origin is the lower-left corner of the
96.52 x 76.20 mm board; Z=0 is the inside floor of the base.

Coordinate summary
  board            : X 0..96.52, Y 0..76.20, Z 5.00..6.60
  inside cavity    : X -2.00..98.52, Y -2.00..78.20, Z 0..22.00
  outside          : X -4.40..100.92, Y -4.40..80.60, Z -2.40..24.40
  PCB -> lid       : 17.00 mm (Z 5.00 -> 22.00), i.e. 15.40 above the PCB top
"""

import bpy
import bmesh
import math
import os
from math import radians, sin, cos

# ----------------------------------------------------------------------------
# Parameters (edit these, then re-run)
# ----------------------------------------------------------------------------
BOARD_W = 96.52
BOARD_D = 76.20
BOARD_T = 1.60

MARGIN = 2.00          # board edge to inside wall
WALL = 2.40
FLOOR = 2.40
LID_T = 2.40
PCB_LIFT = 5.00        # solder-side clearance
PCB_TO_LID = 17.00     # PCB underside -> lid underside (measured on the real build)

INNER_TOP = PCB_LIFT + PCB_TO_LID                # 22.00
OUTER_TOP = INNER_TOP + LID_T                    # 24.40

IN_X0, IN_X1 = -MARGIN, BOARD_W + MARGIN          # -2 .. 98.52
IN_Y0, IN_Y1 = -MARGIN, BOARD_D + MARGIN          # -2 .. 78.20
OUT_X0, OUT_X1 = IN_X0 - WALL, IN_X1 + WALL       # -4.4 .. 100.92
OUT_Y0, OUT_Y1 = IN_Y0 - WALL, IN_Y1 + WALL       # -4.4 .. 80.6
R_OUT = 5.0
R_IN = 3.0

# Mounting holes measured from the EasyEDA PCB: 6 x 2.286 mm, M2 screws.
MOUNT_HOLES = [
    (12.70, 5.08), (48.26, 5.08), (83.82, 5.08),
    (12.70, 71.12), (48.26, 71.12), (83.82, 71.12),
]
BOSS_D = 5.0
PILOT_D = 1.65
PILOT_DEPTH = 7.0

# Connector / module coordinates measured from the PCB file.
ESP_ROWS_Y = (43.053, 65.913)          # two 19-pin rows
ESP_HEADER_X = 26.162
ESP_HEADER_LEN = 48.80
ESP_HEADER_W = 2.50
ESP_HEADER_H = 8.50

SENSORS = [
    ("ZE08",  17.145, 10.541, 8.50, 2.00),
    ("SHT40", 48.260, 10.541, 10.20, 2.54),
    ("SGP40", 78.486, 10.541, 10.20, 2.54),
]

# Sensor vent grilles in the lid.  The positions were measured on the assembled
# board (scaled from the six PCB mounting holes) and sit over the sensing
# element of each module, not over its header.
#   (name, cx, cy, field_d, hole_d, pitch)
VENTS = [
    ("ZE08",  17.20, 21.50, 15.0, 1.6, 2.4),
    ("SHT4x", 47.60, 16.60, 10.0, 1.6, 2.4),
    ("SGP40", 77.40, 16.40, 10.0, 1.6, 2.4),
]
VENT_MARGIN = 0.60     # solid rim between the outer holes and the field edge

# Pressing pads on the lid underside that hold each sensor module down in its
# socket.  Heights measured from the physical board.
#   (name, cx, cy, height below the lid)
PRESS_PADS = [
    ("ZE08",  17.145, 10.541, 6.50),
    ("SHT4x", 48.260, 10.541, 1.50),
    ("SGP40", 78.486, 10.541, 1.50),
    ("OLED",  72.009, 66.400, 1.50),
]
PAD_W = 12.0
PAD_D = 7.0
PAD_R = 2.0

# OLED: 0.96 inch 128x64 module, 27.3 x 27.3 mm board, 26.7 x 14.7 mm glass,
# header on the top edge at OLED_HEADER.  Window frames the glass.
OLED_HEADER = (72.009, 65.913)
OLED_WIN_W = 25.0
OLED_WIN_D = 14.0
OLED_WIN_CX = 72.20
OLED_WIN_CY = 54.80
OLED_WIN_R = 1.5

# ESP32 left-end cable opening.  The charging port top is level with the lid
# underside (17 mm above the PCB underside), so the opening is a notch that runs
# up to the lid seam and the friction lip gets a matching gap.
USB_CY = (ESP_ROWS_Y[0] + ESP_ROWS_Y[1]) / 2.0   # 54.483
USB_W = 16.0          # along Y
USB_Z0 = 17.50        # bottom edge
USB_Z1 = INNER_TOP + 1.0                          # runs past the wall top
USB_R = 2.0
USB_GAP_W = USB_W + 2.0
PORT_RELIEF_D = 4.00   # lid edge cut-back above the cable notch

# Lid fixing screws: M2 self-tapping into bosses that hang from the lid seam.
# Each boss sits on a 45 deg cone and is tied to the wall by a 45 deg ramp rib,
# so the base prints without supports.
#   (cx, cy, wall axis)
SCREW_POS = [
    (3.00, 38.10, 'X-'), (93.52, 38.10, 'X+'),
]
BOSS_R = 2.50
BOSS_Z0 = 16.50          # bottom of the full-diameter part
RIB_TOP = INNER_TOP - 3.00
LID_SCREW_D = 2.20       # clearance hole through the lid
LID_PILOT_D = 1.40       # self-tapping pilot in the boss
LID_PILOT_DEPTH = 4.00

# Two push buttons beside the charging port (ESP32 EN / BOOT).
BUTTONS = [(4.50, 46.48), (4.50, 62.48)]
BTN_TOP = PCB_LIFT + 16.00        # switch top, 1.00 below the lid
BTN_HOLE_D = 4.50                 # through hole in the lid
BTN_SHAFT_D = 4.00
BTN_FLANGE_D = 6.60
BTN_FLANGE_T = 0.80
BTN_PROUD = 0.60                  # shaft length above the lid surface

# Lid friction lip
LIP_CLEAR = 0.15      # per side
LIP_T = 1.60
LIP_H = 2.60          # below the lid plate
RIB_OUT = 0.30        # radial interference
RIB_Z0 = INNER_TOP - 1.90
RIB_Z1 = INNER_TOP - 0.95

# ----------------------------------------------------------------------------
# Generic mesh helpers
# ----------------------------------------------------------------------------

def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.curves, bpy.data.materials,
                       bpy.data.cameras, bpy.data.lights):
        for block in list(datablocks):
            if block.users == 0:
                datablocks.remove(block)


def rr_loop(w, d, r, seg=10):
    """CCW rounded-rectangle loop in the XY plane, centred on (0,0)."""
    r = max(0.0, min(r, w / 2.0, d / 2.0))
    pts = []
    corners = [
        ( w / 2 - r,  d / 2 - r, 0.0),
        (-w / 2 + r,  d / 2 - r, 90.0),
        (-w / 2 + r, -d / 2 + r, 180.0),
        ( w / 2 - r, -d / 2 + r, 270.0),
    ]
    for cx, cy, a0 in corners:
        for i in range(seg + 1):
            a = radians(a0 + 90.0 * i / seg)
            pts.append((cx + r * cos(a), cy + r * sin(a)))
    return pts


def make_mesh(name, verts, faces):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.validate(verbose=False)
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    # recalculate normals
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()
    return ob


def solid_prism(name, loop, axis, a0, a1, cx=0.0, cy=0.0):
    """Extrude a 2D closed loop along X or Z.

    axis='Z': loop is (x,y), extruded z=a0..a1, offset by (cx,cy)
    axis='X': loop is (y,z), extruded x=a0..a1, offset by (cy=?,cx=?)
    """
    n = len(loop)
    verts = []
    if axis == 'Z':
        verts += [(x + cx, y + cy, a0) for x, y in loop]
        verts += [(x + cx, y + cy, a1) for x, y in loop]
    else:
        verts += [(a0, x + cx, y + cy) for x, y in loop]
        verts += [(a1, x + cx, y + cy) for x, y in loop]
    faces = []
    faces.append(list(range(n - 1, -1, -1)))          # bottom / start cap
    faces.append(list(range(n, 2 * n)))                # top / end cap
    for i in range(n):
        j = (i + 1) % n
        faces.append([i, j, n + j, n + i])
    return make_mesh(name, verts, faces)


def rounded_box(name, x0, x1, y0, y1, z0, z1, r, seg=10):
    loop = rr_loop(x1 - x0, y1 - y0, r, seg)
    return solid_prism(name, loop, 'Z', z0, z1,
                       cx=(x0 + x1) / 2, cy=(y0 + y1) / 2)


def rounded_box_c(name, cx, cy, w, d, z0, z1, r, seg=10):
    return rounded_box(name, cx - w / 2, cx + w / 2,
                       cy - d / 2, cy + d / 2, z0, z1, r, seg)


def ring_loft(name, profiles, seg=10):
    """Closed hollow ring loft.

    profiles: list of (z, out_w, out_d, out_r, in_w, in_d, in_r)
    """
    levels = []
    for z, ow, od, orad, iw, id_, irad in profiles:
        levels.append((z, rr_loop(ow, od, orad, seg),
                          rr_loop(iw, id_, irad, seg)))
    n = len(levels[0][1])
    verts = []
    for z, outer, inner in levels:
        verts += [(x, y, z) for x, y in outer]
        verts += [(x, y, z) for x, y in inner]
    m = 2 * n
    faces = []
    # side walls
    for k in range(len(levels) - 1):
        base = k * m
        for i in range(n):
            j = (i + 1) % n
            faces.append([base + i, base + j, base + m + j, base + m + i])
            # inner wall, reversed
            faces.append([base + n + i, base + m + n + i,
                          base + m + n + j, base + n + j])
    # caps
    last = (len(levels) - 1) * m
    for i in range(n):
        j = (i + 1) % n
        faces.append([i, i + n, j + n, j])                      # bottom
        faces.append([last + i, last + j, last + n + j, last + n + i])  # top
    return make_mesh(name, verts, faces)


def cylinder(name, cx, cy, r, z0, z1, verts=48):
    # abs(): a cutter built with z0 > z1 would otherwise come out inside-out
    # and turn a boolean difference into a union.
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r,
                                        depth=abs(z1 - z0),
                                        location=(cx, cy, (z0 + z1) / 2))
    ob = bpy.context.object
    ob.name = name
    return ob


def boolean(obj, cutter, op='DIFFERENCE'):
    mod = obj.modifiers.new(name='bool', type='BOOLEAN')
    mod.operation = op
    mod.solver = 'EXACT'
    mod.object = cutter
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter, do_unlink=True)


def join_objects(objects, name):
    bpy.ops.object.select_all(action='DESELECT')
    for ob in objects:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    objects[0].name = name
    return objects[0]


def assign_material(ob, name, color, rough=0.55, metallic=0.0):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = (*color, 1.0)
        mat.use_nodes = True
        bsdf = next((n for n in mat.node_tree.nodes
                     if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf:
            bsdf.inputs['Base Color'].default_value = (*color, 1.0)
            bsdf.inputs['Roughness'].default_value = rough
            bsdf.inputs['Metallic'].default_value = metallic
    ob.data.materials.clear()
    ob.data.materials.append(mat)
    return mat


# ----------------------------------------------------------------------------
# Lid vent grilles
# ----------------------------------------------------------------------------

def vent_grille(name, cx, cy, field_d, hole_d, pitch, z0, z1):
    """Square-grid perforated field, clipped to a circle of `field_d`."""
    r_field = field_d / 2.0
    r_hole = hole_d / 2.0
    r_max = r_field - r_hole - VENT_MARGIN
    n = int(r_max // pitch)
    holes = []
    k = 0
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            x, y = i * pitch, j * pitch
            if math.hypot(x, y) <= r_max + 1e-6:
                holes.append(cylinder(f'{name}_hole_{k}', cx + x, cy + y,
                                      r_hole, z0, z1, 16))
                k += 1
    return join_objects(holes, name)


def sloped_slab(name, along, a0, a1, b0, b1, zb0, zb1, zt):
    """Slab whose underside ramps from zb0 at a0 to zb1 at a1."""
    bpy.ops.mesh.primitive_cube_add(size=1)
    ob = bpy.context.object
    ob.name = name
    for v in ob.data.vertices:
        ua = 1.0 if (v.co.x if along == 'X' else v.co.y) > 0 else 0.0
        ub = 1.0 if (v.co.y if along == 'X' else v.co.x) > 0 else 0.0
        a = a0 + ua * (a1 - a0)
        b = b0 + ub * (b1 - b0)
        z = zt if v.co.z > 0 else zb0 + ua * (zb1 - zb0)
        if along == 'X':
            v.co.x, v.co.y, v.co.z = a, b, z
        else:
            v.co.y, v.co.x, v.co.z = a, b, z
    ob.data.update()
    return ob


def seam_boss(base, name, cx, cy, axis):
    """Boss hanging from the lid seam: cylinder + 45 deg cone + ramp rib.

    The three parts are unioned into the base one at a time; joining them into a
    single self-intersecting cutter first makes Blender's EXACT solver drop
    geometry.
    """
    if axis in ('X-', 'X+'):
        wall = IN_X0 if axis == 'X-' else IN_X1
        if axis == 'X-':
            x0, x1, zb0, zb1 = wall - 0.60, cx, BOSS_Z0 - (cx - wall + 0.60), BOSS_Z0
        else:
            x0, x1, zb0, zb1 = cx, wall + 0.60, BOSS_Z0, BOSS_Z0 - (wall - cx + 0.60)
        rib = sloped_slab(f'{name}_rib', 'X', x0, x1,
                          cy - BOSS_R, cy + BOSS_R, zb0, zb1, RIB_TOP)
    else:
        wall = IN_Y0 if axis == 'Y-' else IN_Y1
        if axis == 'Y-':
            y0, y1, zb0, zb1 = wall - 0.60, cy, BOSS_Z0 - (cy - wall + 0.60), BOSS_Z0
        else:
            y0, y1, zb0, zb1 = cy, wall + 0.60, BOSS_Z0, BOSS_Z0 - (wall - cy + 0.60)
        rib = sloped_slab(f'{name}_rib', 'Y', y0, y1,
                          cx - BOSS_R, cx + BOSS_R, zb0, zb1, RIB_TOP)
    boolean(base, rib, 'UNION')

    cyl = cylinder(f'{name}_cyl', cx, cy, BOSS_R, BOSS_Z0 - 0.60, INNER_TOP, 48)
    boolean(base, cyl, 'UNION')

    bpy.ops.mesh.primitive_cone_add(vertices=48, radius1=0.0, radius2=BOSS_R,
                                    depth=BOSS_R + 0.60,
                                    location=(cx, cy, BOSS_Z0 - BOSS_R / 2.0 + 0.30))
    cone = bpy.context.object
    cone.name = f'{name}_cone'
    boolean(base, cone, 'UNION')


# ----------------------------------------------------------------------------
# Base
# ----------------------------------------------------------------------------

def build_base():
    base = rounded_box('Enclosure_Base',
                       OUT_X0, OUT_X1, OUT_Y0, OUT_Y1,
                       -FLOOR, INNER_TOP, R_OUT, seg=12)
    cavity = rounded_box('cavity',
                         IN_X0, IN_X1, IN_Y0, IN_Y1,
                         -0.01, INNER_TOP + 0.5, R_IN, seg=12)
    boolean(base, cavity)

    # PCB standoffs: overlap the floor slightly so the union is clean.
    for i, (x, y) in enumerate(MOUNT_HOLES):
        boss = cylinder(f'boss_{i}', x, y, BOSS_D / 2, -0.5, PCB_LIFT)
        boolean(base, boss, 'UNION')

    # M2 self-tapping pilot holes
    for i, (x, y) in enumerate(MOUNT_HOLES):
        pilot = cylinder(f'pilot_{i}', x, y, PILOT_D / 2,
                          PCB_LIFT + 0.5, PCB_LIFT - PILOT_DEPTH)
        boolean(base, pilot)

    # Lid screw bosses at the seam + their M2 self-tapping pilots.
    for i, (cx, cy, axis) in enumerate(SCREW_POS):
        seam_boss(base, f'lid_boss_{i}', cx, cy, axis)
        pilot = cylinder(f'lid_pilot_{i}', cx, cy, LID_PILOT_D / 2,
                          INNER_TOP + 0.5, INNER_TOP - LID_PILOT_DEPTH)
        boolean(base, pilot)

    # Left-side cable / USB notch, cut through the left wall and up to the seam.
    usb_loop = rr_loop(USB_W, USB_Z1 - USB_Z0, USB_R, seg=12)   # loop in (Y,Z)
    usb = solid_prism('usb_cut', usb_loop, 'X',
                      OUT_X0 - 1.0, IN_X0 + 1.0,
                      cx=USB_CY, cy=(USB_Z0 + USB_Z1) / 2.0)
    boolean(base, usb)

    # Finger pry notch in the front wall top edge: a 10 mm wide step in the
    # outer face so a screwdriver tip can catch the lid seam.
    notch = rounded_box_c('pry_notch', 48.26, OUT_Y0 + 0.1,
                          10.0, 2.2, INNER_TOP - 3.2, INNER_TOP + 0.4, 0.6, seg=6)
    boolean(base, notch)

    assign_material(base, 'Base_PLA', (0.78, 0.79, 0.80), 0.6)
    return base


# ----------------------------------------------------------------------------
# Lid
# ----------------------------------------------------------------------------

def build_lid():
    lid = rounded_box('Enclosure_Lid',
                      OUT_X0, OUT_X1, OUT_Y0, OUT_Y1,
                      INNER_TOP, OUTER_TOP, R_OUT, seg=12)

    lip_w = (IN_X1 - IN_X0) - 2 * LIP_CLEAR
    lip_d = (IN_Y1 - IN_Y0) - 2 * LIP_CLEAR
    lip_cx = (IN_X0 + IN_X1) / 2
    lip_cy = (IN_Y0 + IN_Y1) / 2
    lip = ring_loft('lip', [
        (INNER_TOP - LIP_H, lip_w, lip_d, R_IN - LIP_CLEAR,
         lip_w - 2 * LIP_T, lip_d - 2 * LIP_T, max(0.8, R_IN - LIP_CLEAR - LIP_T)),
        (INNER_TOP + 0.20, lip_w, lip_d, R_IN - LIP_CLEAR,
         lip_w - 2 * LIP_T, lip_d - 2 * LIP_T, max(0.8, R_IN - LIP_CLEAR - LIP_T)),
    ], seg=12)
    lip.location = (lip_cx, lip_cy, 0)
    boolean(lid, lip, 'UNION')

    # Friction rib with tapered lead-in.  It overlaps the lip by 0.4 mm.
    rib_in_w = lip_w - 0.8
    rib_in_d = lip_d - 0.8
    rib_in_r = max(0.8, R_IN - LIP_CLEAR - 0.4)
    rib = ring_loft('rib', [
        (RIB_Z0, lip_w, lip_d, R_IN - LIP_CLEAR,
         rib_in_w, rib_in_d, rib_in_r),
        (RIB_Z0 + 0.25, lip_w + 2 * RIB_OUT, lip_d + 2 * RIB_OUT, R_IN - LIP_CLEAR + RIB_OUT,
         rib_in_w, rib_in_d, rib_in_r),
        (RIB_Z1 - 0.20, lip_w + 2 * RIB_OUT, lip_d + 2 * RIB_OUT, R_IN - LIP_CLEAR + RIB_OUT,
         rib_in_w, rib_in_d, rib_in_r),
        (RIB_Z1, lip_w, lip_d, R_IN - LIP_CLEAR,
         rib_in_w, rib_in_d, rib_in_r),
    ], seg=12)
    rib.location = (lip_cx, lip_cy, 0)
    boolean(lid, rib, 'UNION')

    # Four relief slits so the lip can flex over the friction rib.
    lip_x0 = lip_cx - lip_w / 2
    lip_x1 = lip_cx + lip_w / 2
    lip_y0 = lip_cy - lip_d / 2
    lip_y1 = lip_cy + lip_d / 2
    slits = [
        (lip_x0 + LIP_T / 2, 38.1, 2.4, 1.4),
        (lip_x1 - LIP_T / 2, 38.1, 2.4, 1.4),
        (48.26, lip_y0 + LIP_T / 2, 1.4, 2.4),
        (48.26, lip_y1 - LIP_T / 2, 1.4, 2.4),
    ]
    for i, (cx, cy, w, d) in enumerate(slits):
        cut = rounded_box_c(f'slit_{i}', cx, cy, w, d,
                            INNER_TOP - LIP_H - 0.5, INNER_TOP + 0.5, 0.5, seg=4)
        boolean(lid, cut)

    # Matching gap in the lip where the cable notch reaches the lid seam.
    gap = rounded_box('lip_gap', OUT_X0 - 1.0, -0.05,
                      USB_CY - USB_GAP_W / 2.0, USB_CY + USB_GAP_W / 2.0,
                      INNER_TOP - LIP_H - 0.5, INNER_TOP, 1.0, seg=6)
    boolean(lid, gap)

    # Clearance in the lid edge above the cable notch so the charger's moulded
    # plug body can reach the connector.
    relief = rounded_box('port_relief', OUT_X0 - 0.5,
                         OUT_X0 + PORT_RELIEF_D,
                         USB_CY - USB_GAP_W / 2.0, USB_CY + USB_GAP_W / 2.0,
                         INNER_TOP - 0.10, OUTER_TOP + 0.5, 1.0, seg=6)
    boolean(lid, relief)

    # Pressing pads that keep the sensor modules seated in their sockets.
    for name, cx, cy, h in PRESS_PADS:
        pad = rounded_box_c(f'pad_{name}', cx, cy, PAD_W, PAD_D,
                            INNER_TOP - h, INNER_TOP + 0.80, PAD_R, seg=6)
        boolean(lid, pad, 'UNION')

    # Sensor vent grilles in the top cover: perforated fields over the sensing
    # element of each module so air can reach it without a big open hole.
    for name, cx, cy, field_d, hole_d, pitch in VENTS:
        cut = vent_grille(f'vent_{name}', cx, cy, field_d, hole_d, pitch,
                          INNER_TOP - 0.5, OUTER_TOP + 0.5)
        boolean(lid, cut)

    # OLED display window.
    cut = rounded_box_c('oled_window', OLED_WIN_CX, OLED_WIN_CY,
                        OLED_WIN_W, OLED_WIN_D,
                        INNER_TOP - 0.5, OUTER_TOP + 0.5,
                        OLED_WIN_R, seg=12)
    boolean(lid, cut)

    # Lid fixing screws and the two push-button holes.
    for i, (cx, cy, axis) in enumerate(SCREW_POS):
        cut = cylinder(f'lid_screw_{i}', cx, cy, LID_SCREW_D / 2,
                       INNER_TOP - 0.5, OUTER_TOP + 0.5, 32)
        boolean(lid, cut)

    for i, (cx, cy) in enumerate(BUTTONS):
        cut = cylinder(f'btn_hole_{i}', cx, cy, BTN_HOLE_D / 2,
                       INNER_TOP - 0.5, OUTER_TOP + 0.5, 40)
        boolean(lid, cut)

    assign_material(lid, 'Lid_PLA', (0.22, 0.42, 0.62), 0.5)
    return lid


# ----------------------------------------------------------------------------
# Button plungers
# ----------------------------------------------------------------------------

def build_button():
    shaft = cylinder('Button', 0.0, 0.0, BTN_SHAFT_D / 2.0, 0.0,
                     BTN_FLANGE_T + 3.0, 48)
    flange = cylinder('btn_flange', 0.0, 0.0, BTN_FLANGE_D / 2.0, 0.0,
                      BTN_FLANGE_T, 48)
    ob = join_objects([shaft, flange], 'Button')
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)
    assign_material(ob, 'Button_PLA', (0.90, 0.45, 0.12), 0.5)
    return ob


# ----------------------------------------------------------------------------
# Build / export
# ----------------------------------------------------------------------------

def main():
    clear_scene()
    base = build_base()
    lid = build_lid()
    button = build_button()

    # Quick statistics
    for ob in (base, lid, button):
        me = ob.data
        bm = bmesh.new()
        bm.from_mesh(me)
        non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
        print(f'[{ob.name}] verts={len(me.vertices)} polys={len(me.polygons)} '
              f'non_manifold_edges={non_manifold} '
              f'dim={tuple(round(v, 3) for v in ob.dimensions)}')
        bm.free()

    here = os.path.dirname(os.path.abspath(__file__))
    bpy.ops.object.select_all(action='DESELECT')
    base.select_set(True)
    bpy.context.view_layer.objects.active = base
    bpy.ops.wm.stl_export(filepath=os.path.join(here, 'ESP32AirCheck_enclosure_base.stl'),
                          export_selected_objects=True, global_scale=1.0,
                          use_scene_unit=False, apply_modifiers=True)
    bpy.ops.object.select_all(action='DESELECT')
    lid.select_set(True)
    bpy.context.view_layer.objects.active = lid
    bpy.ops.wm.stl_export(filepath=os.path.join(here, 'ESP32AirCheck_enclosure_lid.stl'),
                          export_selected_objects=True, global_scale=1.0,
                          use_scene_unit=False, apply_modifiers=True)

    # Button: exported at the origin (print twice), then placed in the case.
    bpy.ops.object.select_all(action='DESELECT')
    button.select_set(True)
    bpy.context.view_layer.objects.active = button
    bpy.ops.wm.stl_export(filepath=os.path.join(here, 'ESP32AirCheck_button.stl'),
                          export_selected_objects=True, global_scale=1.0,
                          use_scene_unit=False, apply_modifiers=True)

    for i, (cx, cy) in enumerate(BUTTONS):
        ob = button if i == 0 else button.copy()
        if i:
            ob.data = button.data
            ob.name = f'Button_{i + 1}'
            bpy.context.scene.collection.objects.link(ob)
        ob.location = (cx, cy, BTN_TOP + 0.20)

    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(here, 'ESP32AirCheck_enclosure.blend'))
    print('DONE')


if __name__ == '__main__':
    main()
