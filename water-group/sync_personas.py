"""Synchronize persona markdown files between water-group/personas and Tulpa system prompts."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "water-group" / "personas"
TARGET_DIR = ROOT / "chatlocal" / "prompts" / "mcp_chat"

def sync_personas():
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    
    synced = []
    # Sync from water-group/personas to chatlocal/prompts/mcp_chat
    for src in SOURCE_DIR.glob("*.md"):
        if src.name in ("template.md", "behavior.md", "readme.md"):
            continue
        dst = TARGET_DIR / src.name
        if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
            shutil.copy2(src, dst)
            synced.append(f"-> {src.name}")
            
    # Sync from chatlocal/prompts/mcp_chat to water-group/personas
    for src in TARGET_DIR.glob("*.md"):
        if src.name in ("behavior.md", "readme.md"):
            continue
        dst = SOURCE_DIR / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
            synced.append(f"<- {src.name}")
            
    return synced

if __name__ == "__main__":
    results = sync_personas()
    if results:
        print("Personas synchronized:")
        for r in results:
            print(f"  {r}")
    else:
        print("All personas are up-to-date.")
