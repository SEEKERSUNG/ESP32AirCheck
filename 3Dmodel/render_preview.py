"""Render preview images for the ESP32AirCheck enclosure.

Run after generate_enclosure.py:
  blender -b 3Dmodel/ESP32AirCheck_enclosure.blend --python 3Dmodel/render_preview.py
"""
import bpy
import os
import sys
import math
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import generate_enclosure as gen

# Start from the saved enclosure.
base = bpy.data.objects['Enclosure_Base']
lid = bpy.data.objects['Enclosure_Lid']

# Clean out old preview collections if re-run.
for coll in list(bpy.data.collections):
    if coll.name.startswith('PREVIEW_'):
        for ob in list(coll.objects):
            bpy.data.objects.remove(ob, do_unlink=True)
        bpy.data.collections.remove(coll)
prev = bpy.data.collections.new('PREVIEW_objects')
bpy.context.scene.collection.children.link(prev)


def move_to_collection(ob, coll):
    for c in list(ob.users_collection):
        c.objects.unlink(ob)
    coll.objects.link(ob)
    return ob


def mat(name, color, rough=0.5, metallic=0.0):
    m = bpy.data.materials.get(name)
    if m:
        return m
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = next((n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if b:
        b.inputs['Base Color'].default_value = (*color, 1.0)
        b.inputs['Roughness'].default_value = rough
        b.inputs['Metallic'].default_value = metallic
    return m


def box(name, cx, cy, cz, sx, sy, sz, material, rounded=0.0):
    if rounded > 0:
        x0, x1 = cx - sx / 2, cx + sx / 2
        y0, y1 = cy - sy / 2, cy + sy / 2
        ob = gen.rounded_box(name, x0, x1, y0, y1,
                             cz - sz / 2, cz + sz / 2, rounded, seg=8)
    else:
        bpy.ops.mesh.primitive_cube_add(size=1, location=(cx, cy, cz))
        ob = bpy.context.object
        ob.name = name
        ob.scale = (sx, sy, sz)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    move_to_collection(ob, prev)
    ob.data.materials.clear()
    ob.data.materials.append(material)
    return ob


M_PCB = mat('Preview_PCB', (0.045, 0.18, 0.10), 0.55)
M_HEADER = mat('Preview_Header', (0.035, 0.035, 0.040), 0.55)
M_ESP = mat('Preview_ESP32', (0.05, 0.12, 0.28), 0.45)
M_METAL = mat('Preview_Metal', (0.62, 0.64, 0.67), 0.30, 1.0)
M_OLED = mat('Preview_OLED', (0.015, 0.02, 0.03), 0.28)
M_SENSOR = mat('Preview_Sensor', (0.06, 0.22, 0.16), 0.5)

# ---------------------------------------------------------------------------
# PCB mock-up (not exported)
# ---------------------------------------------------------------------------
pcb_mock = box('PCB', 48.26, 38.10, 5.80, 96.52, 76.20, 1.60, M_PCB, rounded=2.0)
for i, (hx, hy) in enumerate(gen.MOUNT_HOLES):
    hole = gen.cylinder(f'pcb_hole_{i}', hx, hy, gen.PILOT_D / 2 + 0.45,
                        gen.PCB_LIFT - 0.2, gen.PCB_LIFT + gen.BOARD_T + 0.2, 32)
    gen.boolean(pcb_mock, hole)

# 19-pin ESP32 sockets
for i, y in enumerate(gen.ESP_ROWS_Y):
    box(f'ESP_header_{i}', gen.ESP_HEADER_X, y, 6.6 + 8.5 / 2,
        gen.ESP_HEADER_LEN, gen.ESP_HEADER_W, 8.5, M_HEADER)

# 4-pin sensor sockets, 5.7 mm high
for name, x, y, bw, pitch in gen.SENSORS:
    box(f'header_{name}', x, y, 6.6 + 5.7 / 2, bw, 2.5, 5.7, M_HEADER)

# OLED socket
box('header_OLED', gen.OLED_HEADER[0], gen.OLED_HEADER[1],
    6.6 + 5.7 / 2, 10.2, 2.5, 5.7, M_HEADER)

# ESP32 DevKit mock-up lying on the 19-pin sockets
esp_z = 6.6 + 8.5
esp_len, esp_w, esp_t = 54.4, 25.4, 1.6
box('ESP32_board', gen.ESP_HEADER_X + 1.0, 54.483, esp_z + esp_t / 2,
    esp_len, esp_w, esp_t, M_ESP, rounded=1.0)
box('ESP32_shield', gen.ESP_HEADER_X + 3.0, 54.483, esp_z + esp_t + 1.5,
    40.0, 18.0, 3.0, M_METAL, rounded=0.8)
box('ESP32_USB', 3.8, 54.483, esp_z + esp_t + 3.5 / 2,
    4.4, 7.5, 3.5, M_METAL, rounded=0.3)
box('ESP32_reset', 7.5, 64.0, esp_z + esp_t + 0.5,
    4.0, 4.0, 2.0, M_METAL, rounded=0.3)

# ---------------------------------------------------------------------------
# Modules sitting on the headers (measured on the assembled board)
# ---------------------------------------------------------------------------
# OLED: 27.3 x 27.3 board on the 8.5 mm 4-pin socket, glass 26.7 x 14.7.
oled_base = 6.6 + 8.5
box('OLED_module', 72.0, 53.05, oled_base + 2.0, 27.3, 27.3, 4.0, M_OLED, rounded=1.0)
box('OLED_glass', gen.OLED_WIN_CX, gen.OLED_WIN_CY,
    oled_base + 4.05, 26.7, 14.7, 0.10,
    mat('Preview_OLED_glass', (0.005, 0.005, 0.01), 0.1))

# Sensors are flat modules on 5.7 mm sockets.  ZE08 top sits 13 mm above the
# PCB underside (Z = 18.0), as measured on the real board.
M_CELL = mat('Preview_Cell', (0.86, 0.34, 0.10), 0.55)
box('sensor_ZE08', 17.20, 20.80, 6.6 + 5.7 + 0.8, 17.9, 20.5, 1.6,
    M_SENSOR, rounded=0.4)
box('ZE08_cell', 17.20, 21.50, 6.6 + 5.7 + 1.6 + 2.05, 10.0, 10.0, 4.1,
    M_CELL, rounded=1.2)
box('sensor_SHT4x', 48.26, 16.10, 6.6 + 5.7 + 1.5, 10.2, 11.1, 3.0,
    M_HEADER, rounded=0.4)
box('SHT4x_port', 47.00, 17.30, 6.6 + 5.7 + 3.0 + 0.15, 3.8, 3.8, 0.30, M_METAL)
box('sensor_SGP40', 78.49, 16.10, 6.6 + 5.7 + 1.75, 11.6, 11.2, 3.5,
    M_ESP, rounded=0.4)
box('SGP40_port', 76.40, 17.00, 6.6 + 5.7 + 3.5 + 0.15, 3.6, 3.6, 0.30, M_METAL)

lid_group = [lid]
sensor_group = []

# ---------------------------------------------------------------------------
# Ground, lights, camera
# ---------------------------------------------------------------------------
ground_mat = mat('Preview_Ground', (0.86, 0.88, 0.90), 0.9)
bpy.ops.mesh.primitive_plane_add(size=600, location=(48.26, 38.1, -2.42))
ground = bpy.context.object
ground.name = 'Preview_Ground'
move_to_collection(ground, prev)
ground.data.materials.append(ground_mat)

world = bpy.data.worlds.get('Preview_World') or bpy.data.worlds.new('Preview_World')
bpy.context.scene.world = world
world.use_nodes = True
bg = next((n for n in world.node_tree.nodes if n.type == 'BACKGROUND'), None)
if bg is None:
    out = next((n for n in world.node_tree.nodes if n.type == 'OUTPUT_WORLD'), None)
    bg = world.node_tree.nodes.new('ShaderNodeBackground')
    world.node_tree.links.new(bg.outputs['Background'], out.inputs['Surface'])
bg.inputs['Color'].default_value = (0.80, 0.85, 0.92, 1.0)
bg.inputs['Strength'].default_value = 0.35


def area_light(name, loc, energy, size, color=(1, 1, 1)):
    data = bpy.data.lights.new(name, 'AREA')
    data.energy = energy
    data.shape = 'DISK'
    data.size = size
    data.color = color
    ob = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    d = Vector((48.26, 38.1, 8.0)) - Vector(loc)
    ob.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
    return ob


sun_data = bpy.data.lights.new('Sun', 'SUN')
sun_data.energy = 1.5
sun_data.angle = math.radians(12)
sun = bpy.data.objects.new('Sun', sun_data)
bpy.context.scene.collection.objects.link(sun)
sun.rotation_euler = (math.radians(42), math.radians(-12), math.radians(-35))

area_light('Key', (-70, -110, 180), 70, 150, (1.0, 0.96, 0.90))
area_light('Fill', (170, -60, 100), 28, 130, (0.82, 0.90, 1.0))
area_light('Rim', (60, 170, 140), 45, 110, (0.9, 0.95, 1.0))

cam_data = bpy.data.cameras.new('Preview_Camera')
cam = bpy.data.objects.new('Preview_Camera', cam_data)
bpy.context.scene.collection.objects.link(cam)
bpy.context.scene.camera = cam
cam_data.lens = 58
cam_data.sensor_width = 36
cam_data.clip_start = 1
cam_data.clip_end = 2000


def look_at(loc, target):
    cam.location = loc
    d = Vector(target) - Vector(loc)
    cam.rotation_mode = 'XYZ'
    cam.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()


scene = bpy.context.scene
scene.render.engine = 'BLENDER_EEVEE'
scene.render.resolution_x = 1400
scene.render.resolution_y = 1050
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
try:
    scene.eevee.taa_render_samples = 48
except Exception:
    pass
scene.view_settings.view_transform = 'Standard'
try:
    scene.view_settings.look = 'None'
except Exception:
    pass
scene.view_settings.exposure = -0.2

# ---------------------------------------------------------------------------
# Renders
# ---------------------------------------------------------------------------
def render(name):
    scene.render.filepath = os.path.join(HERE, name)
    bpy.ops.render.render(write_still=True)
    print('RENDERED', scene.render.filepath)


# 1) assembled exterior, three-quarter front-left
for ob in sensor_group:
    ob.hide_render = True
for ob in lid_group:
    ob.location.z -= 0.0
look_at((-110, -175, 185), (48, 38, 8))
render('preview_assembly.png')

# 2) exploded, lid lifted, PCB visible
lift = 62.0
for ob in lid_group:
    ob.location.z += lift
for ob in sensor_group:
    ob.hide_render = False
look_at((-125, -205, 245), (48, 38, 42))
render('preview_exploded.png')

# 3) top view of the lid / openings
for ob in lid_group:
    ob.location.z -= lift
for ob in sensor_group:
    ob.hide_render = True
cam_data.type = 'ORTHO'
cam_data.ortho_scale = 125
cam.rotation_mode = 'XYZ'
cam.location = (48.26, 38.10, 220)
cam.rotation_euler = (0, 0, 0)
render('preview_top.png')

# 4) base only, tilted look into the shell: six M2 bosses + 5 mm PCB lift
for ob in bpy.data.objects:
    if ob.type == 'MESH' and ob.name not in ('Enclosure_Base',):
        ob.hide_render = True
cam_data.type = 'ORTHO'
cam_data.ortho_scale = 118
sun.rotation_euler = (math.radians(20), math.radians(-6), math.radians(-30))
sun_data.energy = 3.0
bg.inputs['Strength'].default_value = 0.25
scene.view_settings.exposure = -0.45
look_at((48.26, 26.0, 330), (48.26, 38.10, 6.0))
render('preview_base_top.png')

# 5) lid from below: friction lip, relief slits, cable notch gap, cut-outs
bpy.data.objects['Enclosure_Base'].hide_render = True
lid.hide_render = False
ground.hide_render = True
for ob in bpy.data.objects:
    if ob.type == 'LIGHT' and ob.data.type == 'AREA':
        ob.hide_render = True
under = area_light('Under', (48.26, 38.1, -110), 70, 150)
sun.rotation_euler = (math.radians(180), 0.0, 0.0)
sun_data.energy = 1.0
cam_data.type = 'ORTHO'
cam_data.ortho_scale = 118
cam.location = (48.26, 38.10, -80)
cam.rotation_euler = (math.radians(180), 0.0, 0.0)
render('preview_lid_bottom.png')

# Save a preview blend as well.
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, 'ESP32AirCheck_preview.blend'))
print('PREVIEW DONE')
