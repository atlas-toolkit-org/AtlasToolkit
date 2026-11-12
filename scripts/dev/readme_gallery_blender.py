"""
A clay render of one mesh, for the README's gallery. Runs inside Blender, started by readme_gallery.py:

    blender -b --factory-startup --python readme_gallery_blender.py -- <mesh> <out.png> <width> <height> <theta> <psi>

Theta and psi are the drawing tools' camera angles, so the shaded panel looks from the same side as the line panel.
"""
import math
import sys

import bpy
import numpy as np
from mathutils import Vector


def import_mesh(path: str):
    low = path.lower()
    if low.endswith(".stl"):
        bpy.ops.wm.stl_import(filepath=path, forward_axis="Y", up_axis="Z")
    elif low.endswith(".obj"):
        bpy.ops.wm.obj_import(filepath=path, forward_axis="Y", up_axis="Z")
    else:
        bpy.ops.wm.ply_import(filepath=path, forward_axis="Y", up_axis="Z")
    return list(bpy.context.selected_objects)


def use_gpu_if_any(scene) -> None:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = kind
            prefs.get_devices()
            devices = [d for d in prefs.devices if d.type == kind]
            if devices:
                for d in devices:
                    d.use = True
                scene.cycles.device = "GPU"
                return
        except (TypeError, ValueError):
            continue


def clay() -> bpy.types.Material:
    mat = bpy.data.materials.new("clay")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.46, 0.45, 0.43, 1)
    bsdf.inputs["Roughness"].default_value = 0.62
    return mat


def add_sun(name: str, energy: float, rotation, angle: float) -> None:
    data = bpy.data.lights.new(name, "SUN")
    data.energy = energy
    data.angle = math.radians(angle)
    obj = bpy.data.objects.new(name, data)
    obj.rotation_euler = rotation
    bpy.context.scene.collection.objects.link(obj)


def main() -> None:
    path, out, width, height, theta, psi = sys.argv[sys.argv.index("--") + 1:]
    width, height = int(width), int(height)
    theta, psi = math.radians(float(theta)), math.radians(float(psi))

    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)

    objects = import_mesh(path)
    material = clay()
    corners, points = [], []
    for obj in objects:
        obj.data.materials.clear()
        obj.data.materials.append(material)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.shade_auto_smooth(angle=math.radians(35))
        corners += [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        xyz = np.empty(len(obj.data.vertices) * 3)
        obj.data.vertices.foreach_get("co", xyz)
        matrix = np.array(obj.matrix_world)
        points.append(xyz.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3])

    low = Vector(tuple(min(c[i] for c in corners) for i in range(3)))
    high = Vector(tuple(max(c[i] for c in corners) for i in range(3)))
    centre = (low + high) / 2
    size = max((high - low).length, 1e-6)

    # A floor that only catches the shadow, so the model sits on the panel's paper.
    bpy.ops.mesh.primitive_plane_add(size=size * 40, location=(centre.x, centre.y, low.z - size * 0.002))
    bpy.context.object.is_shadow_catcher = True

    # Same convention as the drawing tools: psi 180 looks at the front (-Y).
    direction = Vector((math.sin(theta) * math.sin(psi), math.sin(theta) * math.cos(psi), math.cos(theta)))
    data = bpy.data.cameras.new("camera")
    camera = bpy.data.objects.new("camera", data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    data.lens_unit = "FOV"
    data.sensor_fit = "VERTICAL" if height >= width else "HORIZONTAL"
    data.angle = math.radians(26)
    data.clip_start = size * 0.01
    data.clip_end = size * 100
    camera.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()

    # Aim at the model's middle as seen from here, then move back until all of it is in frame, with a margin.
    aspect = width / height
    tan_y = math.tan(data.angle / 2) * (1 if height >= width else 1 / aspect)
    tan_x = tan_y * aspect
    rot = camera.rotation_euler.to_matrix()
    right, up, forward = rot @ Vector((1, 0, 0)), rot @ Vector((0, 1, 0)), rot @ Vector((0, 0, -1))
    cloud = np.concatenate(points)
    across = cloud @ np.array(right)
    high_low = cloud @ np.array(up)
    along = cloud @ np.array(forward)
    aim = right * float((across.min() + across.max()) / 2) + up * float((high_low.min() + high_low.max()) / 2)
    aim += forward * float((along.min() + along.max()) / 2)
    deep = along - float(aim.dot(forward))
    wide = np.abs(across - float(aim.dot(right)))
    tall = np.abs(high_low - float(aim.dot(up)))
    distance = float(np.max(np.maximum(wide / (tan_x * 0.90), tall / (tan_y * 0.90)) - deep))
    camera.location = aim - forward * distance

    # Light from the viewer's upper left, whichever side the camera is on.
    world = bpy.data.worlds.new("world")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (1, 1, 1, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.5
    key = (forward * -0.75 - right * 0.65 + Vector((0, 0, 1.25))).normalized()
    add_sun("key", 2.1, (-key).to_track_quat("-Z", "Y").to_euler(), 30)
    fill = (forward * -0.5 + right * 0.8 + Vector((0, 0, 0.1))).normalized()
    add_sun("fill", 0.55, (-fill).to_track_quat("-Z", "Y").to_euler(), 40)

    scene.render.engine = "CYCLES"
    use_gpu_if_any(scene)
    scene.cycles.samples = 96
    scene.cycles.use_denoising = True
    scene.render.film_transparent = True
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    scene.render.filepath = out
    bpy.ops.render.render(write_still=True)


main()
