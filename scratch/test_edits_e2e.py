import requests

BASE_URL = "http://127.0.0.1:8000/api/v1"

# 1. Fetch project list
r = requests.get(f"{BASE_URL}/projects")
print("Projects status:", r.status_code)
projects = r.json()
if not projects:
    print("No projects found.")
    exit(0)

proj_id = projects[0]["project_id"]
print(f"Testing with project: {proj_id}")

# 2. Get current project
r_proj = requests.get(f"{BASE_URL}/projects/{proj_id}")
proj_data = r_proj.json()
segments = proj_data["timeline"]["segments"]
print(f"Initial segments count: {len(segments)}")
for idx, s in enumerate(segments):
    print(f"  Segment {idx+1}: [{s['source_start']}s - {s['source_end']}s] duration: {s.get('timeline_duration')}")

# 3. Test cutting 2 seconds from the start
print("\nApplying cut edit (0.0 to 2.0)...")
r_cut = requests.post(
    f"{BASE_URL}/projects/{proj_id}/edits",
    headers={"Idempotency-Key": "test_e2e_cut_1"},
    json={
        "operations": [
            {
                "operation": "cut",
                "start_time": 0.0,
                "end_time": 2.0,
                "time_policy": "SOURCE_TIME"
            }
        ]
    }
)
cut_data = r_cut.json()
print("Cut status:", r_cut.status_code)
print(f"After cut duration: {cut_data.get('after_duration_s')}s (delta: {cut_data.get('duration_delta_s')}s)")
for idx, s in enumerate(cut_data.get("edl", [])):
    print(f"  Segment {idx+1}: [{s['source_start']}s - {s['source_end']}s] duration: {s.get('timeline_duration')}")

# 4. Test Undo
print("\nTesting Undo...")
r_undo = requests.post(f"{BASE_URL}/projects/{proj_id}/edits/undo")
undo_data = r_undo.json()
print("Undo status:", r_undo.status_code)
print(f"After undo duration: {undo_data.get('duration_s')}s")
for idx, s in enumerate(undo_data.get("edl", [])):
    print(f"  Restored Segment {idx+1}: [{s['source_start']}s - {s['source_end']}s] duration: {s.get('timeline_duration')}")

# 5. Test Redo
print("\nTesting Redo...")
r_redo = requests.post(f"{BASE_URL}/projects/{proj_id}/edits/redo")
redo_data = r_redo.json()
print("Redo status:", r_redo.status_code)
print(f"After redo duration: {redo_data.get('duration_s')}s")

# 6. Revert back to clean initial state with undo
r_clean = requests.post(f"{BASE_URL}/projects/{proj_id}/edits/undo")
print("Cleaned back to initial state, duration:", r_clean.json().get('duration_s'))
