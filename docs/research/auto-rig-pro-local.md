# Auto-Rig Pro ในเครื่อง: ข้อมูลสำหรับทำรายการ Retarget Bone

สถานะ: เก็บข้อมูลเบื้องต้นแล้ว ยังไม่ได้สร้างหรือแก้สคริปต์สำหรับ retarget

วันที่สำรวจ: 2026-07-18

## Installation ที่ยืนยันจากภาพของผู้ใช้

Auto-Rig Pro ที่ผู้ใช้เปิดใช้อยู่คือ installation ของ Blender 5.1:

`C:\Users\char8\AppData\Roaming\Blender Foundation\Blender\5.1\extensions\user_default\auto_rig_pro\__init__.py`

จาก manifest ของ installation นี้:

- Add-on id: `auto_rig_pro`
- ชื่อ: `Auto-Rig Pro`
- Version: `3.77.10`
- ประเภท: Blender Extension / add-on
- ผู้ดูแลใน manifest: Artell
- Blender ขั้นต่ำ: `2.8.0`
- URL หลัก: `https://www.lucky3d.fr/auto-rig-pro/doc/`

หลักฐาน: [blender_manifest.toml](<C:/Users/char8/AppData/Roaming/Blender Foundation/Blender/5.1/extensions/user_default/auto_rig_pro/blender_manifest.toml>) และ [__init__.py](<C:/Users/char8/AppData/Roaming/Blender Foundation/Blender/5.1/extensions/user_default/auto_rig_pro/__init__.py>)

## Installation อื่นที่พบ

พบ Auto-Rig Pro ใน profile อื่นด้วย:

| Blender profile | Version | Path |
|---|---:|---|
| 5.0 | 3.77.10 | `C:\Users\char8\AppData\Roaming\Blender Foundation\Blender\5.0\extensions\user_default\auto_rig_pro` |
| 5.1 | 3.77.10 | `C:\Users\char8\AppData\Roaming\Blender Foundation\Blender\5.1\extensions\user_default\auto_rig_pro` |
| 5.1_BK2 | 3.78.10 | `C:\Users\char8\AppData\Roaming\Blender Foundation\Blender\5.1_BK2\extensions\user_default\auto_rig_pro` |

`5.1_BK2` เป็นชุดที่ใหม่กว่า แต่ภาพของผู้ใช้ชี้ไปที่ profile `5.1` ดังนั้นถ้าจะทำสคริปต์ให้ตรงกับ Blender ที่กำลังใช้ ควรอ้างอิง `5.1` เป็นหลัก และใช้ `5.1_BK2` เป็นข้อมูลเปรียบเทียบภายหลัง

หลักฐาน: manifest และ `00_LOG.txt` ของแต่ละ installation

## จุดสำคัญของระบบ Remap / Retarget

Source หลักคือ [src/auto_rig_remap.py](<C:/Users/char8/AppData/Roaming/Blender Foundation/Blender/5.1/extensions/user_default/auto_rig_pro/src/auto_rig_remap.py>)

### UI และลำดับการใช้งาน

แผง UI อยู่ใน View 3D Sidebar หมวด `ARP` ชื่อ `Auto-Rig Pro: Remap` โดยลำดับหลักคือ:

1. เลือก `Source Armature`
2. เลือก `Target Armature`
3. กด `Build Bones List`
4. ตรวจ/แก้รายการ Source Bones → Target Bones
5. ถ้าจำเป็น ตั้งค่า root, IK, pole และ location เป็นรายกระดูก
6. Import/Export mapping preset ได้
7. กด `Re-Target`

Operator สำคัญที่พบ:

| Operator | หน้าที่ |
|---|---|
| `arp.build_bones_list` | สร้างรายการกระดูก source/target และลองจับคู่ชื่ออัตโนมัติ |
| `arp.retarget` | ทำ retarget และ bake animation |
| `arp.retarget_bind_only` | bind หรือ unbind โดยไม่ทำ bake เต็มขั้นตอน |
| `arp.import_config` | import ไฟล์ `.bmap` |
| `arp.export_config` | export รายการเป็น `.bmap` |
| `arp.import_config_preset` | import preset จากโฟลเดอร์ preset |
| `arp.mirror_bones_list` | mirror mapping ซ้าย/ขวา |
| `arp.remap_enable_all_actions` / `arp.remap_disable_all_actions` | เปิด/ปิด action สำหรับการ retarget แบบหลาย animation |
| `arp.batch_retarget` | retarget หลาย animation |

### ข้อมูลที่เก็บใน Scene

Auto-Rig Pro ลงทะเบียน properties เหล่านี้ใน `bpy.types.Scene`:

- `source_rig`: ชื่อ armature ต้นทาง
- `target_rig`: ชื่อ armature ปลายทาง
- `source_action`: action ต้นทาง
- `remap_source_nodes`: รายการชื่อกระดูก source
- `bones_map_v2`: collection ของรายการ mapping รุ่นปัจจุบัน
- `bones_map_index`: index ของรายการที่เลือกใน UI
- `global_scale`: scale ระหว่าง source และ target
- `search_and_replace`, `name_search`, `name_replace`: ใช้แทน namespace/prefix ของชื่อกระดูกตอน import preset
- `arp_retarget_in_place`: ชดเชย root motion สำหรับ animation แบบ cyclic
- `batch_retarget`: โหมด retarget หลาย animation

`bones_map` และ `source_nodes_name_string` ยังถูกเก็บไว้เพื่อ backward compatibility แต่ระบบปัจจุบันใช้ `bones_map_v2` และ `remap_source_nodes`

### รูปแบบของหนึ่งรายการ mapping

`BoneRemapSettingsv2` มีแนวคิดว่า `source_bone` คือกระดูกต้นทาง และ property `name` ที่มากับ `PropertyGroup` ของ Blender ใช้เป็นชื่อกระดูก target ในแถวเดียวกัน

ข้อมูลหลักต่อแถว:

- `source_bone`: ชื่อกระดูก source
- `name`: ชื่อกระดูก target
- `set_as_root`: ใช้กระดูกนี้เป็น root/hips; มีได้เพียงหนึ่งรายการ
- `ik`: ใช้ IK remapping
- `ik_pole`: ชื่อ IK pole bone (ถ้ามี)
- `ik_world`: ใช้ IK world-space
- `ik_auto_pole`: `ABSOLUTE`, `RELATIVE_TARGET` หรือ `RELATIVE_CHAIN`
- `ik_create_constraints`: สร้าง IK constraint อัตโนมัติถ้ายังไม่มี
- `ik_1`, `ik_2`: ชื่อกระดูกใน chain ของ IK
- `location`: remap location แบบ local relative กับ parent
- `IK_axis_correc`: แกนแก้ไขแนว IK (`X`, `Y`, `Z`, `-X`, `-Y`, `-Z`)
- `rot_add`, `loc_add`: offset rotation/location
- `loc_mult`: ตัวคูณ location
- `rot_add_bind`, `loc_add_bind`: offset ที่ใช้ใน bind mode

## Logic ของ `Build Bones List`

ฟังก์ชัน `_build_bones_list()` ทำงานโดยสรุปดังนี้:

1. ล้าง `bones_map_v2` และ `remap_source_nodes` เดิม
2. อ่านกระดูกจาก source armature
3. ถ้า source เป็น Auto-Rig Pro จะตัด controller/special bones บางกลุ่มออก เช่น `c_p_`, `c_foot_bank_`, controller สำหรับ roll และ rotation นิ้วบางตัว
4. เก็บ source names แล้วเรียงตามตัวอักษร
5. สร้าง mapping row หนึ่งแถวต่อ source bone โดย default target เป็น `None`
6. อ่าน target pose bones
7. ถ้า target เป็น Auto-Rig Pro จะพิจารณา controller ที่ขึ้นต้นด้วย `c_` หรือมี custom property `cc`; ถ้าไม่ใช่ ARP จะพิจารณา pose bones ทั้งหมด
8. พยายามจับคู่ชื่อแบบมีลำดับความสำคัญ เช่น `head`, `neck`, `spine`, `hip/pelvis`, `shoulder/collar/clavicle`, `upperarm`, `forearm`, `hand`, `thigh`, `shin/calf`, `foot`, `toe/ball` และนิ้ว `thumb/index/middle/ring/pinky`
9. แยกด้านซ้าย/ขวาจากรูปแบบ `left/right`, `.l/.r`, `_l/_r`, `-l/-r`, `l_ / r_` และรูปแบบใกล้เคียง
10. ถ้ายังจับคู่ไม่ได้ ใช้ `difflib.SequenceMatcher` หา fuzzy match โดยเริ่ม threshold `0.5` และปรับลงได้ถึงมากกว่า `0.3`
11. ป้องกันไม่ให้ target bone เดียวถูก assign ซ้ำ และพยายามแก้ side ให้ตรงกันหลัง fuzzy match

ข้อสังเกตสำคัญ: Auto-Rig Pro ไม่ได้เพียงสร้าง list ชื่อกระดูก แต่เลือก target list แตกต่างกันตามชนิดของ rig และมี special case สำหรับ ARP controllers ดังนั้นสคริปต์ที่จะทำ list ให้ง่ายขึ้นควรมีโหมด `ARP target` แยกจาก `generic armature target`

## รูปแบบไฟล์ `.bmap`

ไฟล์ mapping preset อยู่ในโฟลเดอร์ `remap_presets` และ `_export_config()` เขียนข้อมูลหนึ่ง mapping เป็น 5 บรรทัด:

```text
target%location%ik_auto_pole%rot_add%loc_add%loc_mult%ik_create_constraints%ik_world%IK_axis_correc%
source_bone
set_as_root
ik
ik_pole
```

ไฟล์รุ่นเก่าอาจใช้บรรทัดแรกเป็นชื่อ target อย่างเดียว แล้วตามด้วย source/root/ik/pole; importer ยังรองรับรูปแบบนี้อยู่

ตัวอย่างจาก preset ที่ติดตั้ง:

- `mixamo_ik.bmap`: มี mapping เช่น `Hips` → `c_root_master.x` และมีค่า `Set as Root`
- `arp.bmap`: รายการกระดูก controller/deform ของ ARP เอง

## Preset ที่มีในเครื่อง

จาก `5.1_BK2` ซึ่งเป็นชุด Auto-Rig Pro รุ่น 3.78.10 พบ preset เหล่านี้:

`advanced_skeleton`, `arp`, `character_creator`, `daz`, `deepmotion`, `heat_fk`, `heat_ik`, `mblab`, `mixamo_fbx_ik`, `mixamo_fk`, `mixamo_ik`, `mocopi`, `moveoneai`, `perception_neuron`, `rigify`, `rokoko_legs_ik`, `rokoko_legs_ik_2`, `unity_export`, `unreal_mannequin_remap`, `xsens`

ใน installation 5.1 ตามภาพ ให้ดูที่:

`C:\Users\char8\AppData\Roaming\Blender Foundation\Blender\5.1\extensions\user_default\auto_rig_pro\remap_presets`

หลักฐาน: [remap_presets](<C:/Users/char8/AppData/Roaming/Blender Foundation/Blender/5.1/extensions/user_default/auto_rig_pro/remap_presets>) และ [auto_rig_remap.py](<C:/Users/char8/AppData/Roaming/Blender Foundation/Blender/5.1/extensions/user_default/auto_rig_pro/src/auto_rig_remap.py>)

## Extension ที่เกี่ยวข้องแต่แยกจากตัวหลัก

พบ `ARP_rig_tools` แยกต่างหากใน Blender profiles เป็น version `3.77.10` ชื่อใน manifest คือ `Auto-Rig Pro Tools` และระบุชัดว่าเป็นเครื่องมือเสริมสำหรับ operators/snap IK-FK ไม่ใช่ตัวหลักสำหรับสร้าง mapping list

พบ `auto_rig_pro_quick_rig` ใน Blender 5.1 เป็น version `1.27.21` ใช้สร้าง Auto-Rig Pro armature จาก existing skeleton แต่ไม่ใช่ระบบ remap list หลักที่ต้องใช้สำหรับงานนี้

## ความเกี่ยวข้องกับ ScriptToolkit โปรเจกต์นี้

โปรเจกต์นี้มี [biped_names.py](../../biped_names.py) ซึ่ง normalize ชื่อกระดูกเป็น suffix `.L`/`.R` และเก็บ mapping เดิมไว้ใน custom properties:

- `Armature.data["biped_name_mapping"]`
- `Object["biped_vg_mapping"]` สำหรับ vertex groups

เครื่องมือนี้อาจนำมาใช้เป็นขั้นเตรียมชื่อก่อนทำ remap list ได้ แต่ปัจจุบันยังไม่มีโค้ดที่เชื่อมกับ Auto-Rig Pro `Scene.bones_map_v2` หรือไฟล์ `.bmap`

## แนวทางที่ควรเก็บไว้สำหรับขั้นทำสคริปต์ภายหลัง

- อ่าน `Scene.bones_map_v2` โดยตรงเพื่อแสดง/ตรวจสอบ mapping ที่ ARP ใช้อยู่ แทนการเดาจาก UI
- รองรับทั้ง `source_bone` และ target `name`
- เก็บ options สำคัญของแต่ละแถว โดยเฉพาะ root, IK, pole, location และ offset
- ใช้ `.bmap` เป็น format สำหรับบันทึก preset เพราะ ARP มี importer/exporter อยู่แล้ว
- ทำชื่อ preset/rig profile ให้สัมพันธ์กับ source/target armature และ Blender profile
- แยกโหมด `ARP target` กับ generic target เพื่อไม่แสดง controller/special bones ที่ ARP ไม่ใช้ในการ retarget
- ใช้ mirror logic ของ ARP หรือทำ normalization ของ left/right ให้ชัดเจนก่อน auto-match
- แสดง unmatched, duplicate target และ fuzzy match confidence เป็นรายการตรวจสอบก่อนกด retarget

ยังไม่ได้ทำในรอบนี้: สร้าง operator, panel, exporter ใหม่, แก้ Auto-Rig Pro source, แก้ ScriptToolkit source หรือเขียนไฟล์ `.bmap` ใด ๆ
