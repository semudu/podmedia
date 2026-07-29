import sqlite3
import json
import os
import subprocess

RADARR_DB = os.path.join(os.path.dirname(__file__), 'services', 'tools', 'radarr', 'config', 'radarr.db')
SONARR_DB = os.path.join(os.path.dirname(__file__), 'services', 'tools', 'sonarr', 'config', 'sonarr.db')

def update_radarr_db():
    """Applies optimal quality definitions and profile filtering directly to Radarr database."""
    if not os.path.exists(RADARR_DB):
        return

    conn = sqlite3.connect(RADARR_DB)
    cursor = conn.cursor()

    # Cap 720p/1080p/WEB/Bluray quality definitions at MaxSize = 65.0 MB/min (~7.8 GB per 2-hour movie)
    cursor.execute("""
        UPDATE QualityDefinitions 
        SET MaxSize = 65.0 
        WHERE Title LIKE '%720%' OR Title LIKE '%1080%' OR Title LIKE '%Bluray%' OR Title LIKE '%WEB%'
    """)
    print(f"[✓] Radarr 720p/1080p size limits updated to 65.0 MB/min (~7.8 GB). Affected qualities: {cursor.rowcount}")

    # Map quality IDs for 4K / REMUX
    cursor.execute("SELECT Id, Title FROM QualityDefinitions")
    disallowed_ids = set()
    for q_id, q_title in cursor.fetchall():
        if any(k in q_title for k in ["2160", "REMUX", "Remux", "4K", "BR-DISK", "Raw-HD"]):
            disallowed_ids.add(q_id)

    cursor.execute("SELECT Id, Name, Items FROM QualityProfiles")
    profiles = cursor.fetchall()

    for p_id, p_name, p_items_json in profiles:
        if not p_items_json:
            continue
        try:
            items = json.loads(p_items_json)
            def process_items(item_list):
                for item in item_list:
                    if isinstance(item, dict):
                        q_id = item.get("quality")
                        if isinstance(q_id, int) and q_id in disallowed_ids:
                            item["allowed"] = False
                        if "items" in item and isinstance(item["items"], list):
                            process_items(item["items"])

            process_items(items)
            cursor.execute("UPDATE QualityProfiles SET Items = ? WHERE Id = ?", (json.dumps(items), p_id))
            print(f"[✓] Radarr Quality Profile updated: {p_name}")
        except Exception as e:
            print(f"Radarr profile error ({p_name}): {e}")

    conn.commit()
    conn.close()

def update_sonarr_db():
    """Applies optimal quality definitions and profile filtering directly to Sonarr database."""
    if not os.path.exists(SONARR_DB):
        return

    conn = sqlite3.connect(SONARR_DB)
    cursor = conn.cursor()

    # Cap 720p/1080p/WEB/Bluray quality definitions at MaxSize = 45.0 MB/min (~2.0 GB per 45-min episode)
    cursor.execute("""
        UPDATE QualityDefinitions 
        SET MaxSize = 45.0 
        WHERE Title LIKE '%720%' OR Title LIKE '%1080%' OR Title LIKE '%Bluray%' OR Title LIKE '%WEB%'
    """)
    print(f"[✓] Sonarr 720p/1080p size limits updated to 45.0 MB/min (~2.0 GB). Affected qualities: {cursor.rowcount}")

    cursor.execute("SELECT Id, Title FROM QualityDefinitions")
    disallowed_ids = set()
    for q_id, q_title in cursor.fetchall():
        if any(k in q_title for k in ["2160", "REMUX", "Remux", "4K", "BR-DISK", "Raw-HD"]):
            disallowed_ids.add(q_id)

    cursor.execute("SELECT Id, Name, Items FROM QualityProfiles")
    profiles = cursor.fetchall()

    for p_id, p_name, p_items_json in profiles:
        if not p_items_json:
            continue
        try:
            items = json.loads(p_items_json)
            def process_items(item_list):
                for item in item_list:
                    if isinstance(item, dict):
                        q_id = item.get("quality")
                        if isinstance(q_id, int) and q_id in disallowed_ids:
                            item["allowed"] = False
                        if "items" in item and isinstance(item["items"], list):
                            process_items(item["items"])

            process_items(items)
            cursor.execute("UPDATE QualityProfiles SET Items = ? WHERE Id = ?", (json.dumps(items), p_id))
            print(f"[✓] Sonarr Quality Profile updated: {p_name}")
        except Exception as e:
            print(f"Sonarr profile error ({p_name}): {e}")

    # Add ReleaseProfile to block REMUX / BDMV / COMPLETE
    cursor.execute("SELECT Id, Ignored FROM ReleaseProfiles")
    existing_rps = cursor.fetchall()
    has_remux = any("REMUX" in (rp[1] or "") for rp in existing_rps)

    if not has_remux:
        cursor.execute(
            "INSERT INTO ReleaseProfiles (Name, Enabled, Required, Ignored, IndexerId, Tags) VALUES (?, ?, ?, ?, ?, ?)",
            ("Block REMUX & Raw Disks", 1, "[]", '["REMUX","BDMV","COMPLETE.BLURAY"]', 0, "[]")
        )

    conn.commit()
    conn.close()

if __name__ == '__main__':
    print("--- Applying Configuration Limits Directly to Databases ---")
    subprocess.run("podman stop media_center_radarr media_center_sonarr", shell=True, capture_output=True)
    
    update_radarr_db()
    update_sonarr_db()
    
    subprocess.run("podman start media_center_radarr media_center_sonarr", shell=True, capture_output=True)
    print("\n[🎉] Radarr & Sonarr Optimal Limits Applied Successfully!")
