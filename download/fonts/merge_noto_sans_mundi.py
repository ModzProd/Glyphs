#!/usr/bin/env python3
"""
NotoSansMundi Font Merge Workflow
==================================
Merges 180 NotoSans Regular OTF files into a single unified font.

Key constraint: OTF format has a 65535 glyph limit.
Strategy: Only keep glyphs actually used in the final cmap (one glyph per unicode point).
"""

import os, sys, gc, logging
from fontTools.ttLib import TTFont
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('NotoSansMundi')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_DIR = os.path.join(SCRIPT_DIR, "NotoSans_Regular_OTF")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "NotoSansMundi")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "NotoSansMundi-Regular.otf")

CJK_PREFIXES = ["NotoSansCJK"]
MAX_GLYPHS = 65000  # CFF string index needs headroom below 65535


def get_otf_files(source_dir):
    files = []
    for f in sorted(os.listdir(source_dir)):
        if f.endswith(".otf"):
            files.append((f, os.path.join(source_dir, f), os.path.getsize(os.path.join(source_dir, f))))
    files.sort(key=lambda x: x[2])
    return files


def is_cjk(filename):
    return any(filename.startswith(p) for p in CJK_PREFIXES)


def build_merged_font(font_paths, output_path, font_name="NotoSansMundi"):
    """
    Merge multiple OTF fonts into one. Only keeps one glyph per unicode codepoint.
    First font's glyph takes priority for overlapping codepoints.
    """
    logger.info(f"Building merged font from {len(font_paths)} sources...")
    
    # Step 1: Build unified cmap (first font wins for overlapping codepoints)
    logger.info("  Step 1: Building unified cmap...")
    unified_cmap = {}  # unicode -> (glyph_name, font_index)
    
    for i, fpath in enumerate(font_paths):
        fname = os.path.basename(fpath)
        font = TTFont(fpath, lazy=True)
        cmap = font.getBestCmap()
        new_count = 0
        for uv in cmap:
            if uv not in unified_cmap:
                unified_cmap[uv] = (cmap[uv], i)
                new_count += 1
        font.close()
        logger.info(f"    {fname}: {new_count} new unicodes (total: {len(unified_cmap)})")
    
    logger.info(f"  Total unique unicode points: {len(unified_cmap)}")
    
    # Step 2: Group unicode points by font index
    logger.info("  Step 2: Grouping by source font...")
    font_unicode_map = {}  # font_index -> {unicode -> original_glyph_name}
    for uv, (gname, fidx) in unified_cmap.items():
        if fidx not in font_unicode_map:
            font_unicode_map[fidx] = {}
        font_unicode_map[fidx][uv] = gname
    
    # Step 3: Record glyphs, only for the unicode points assigned to each font
    logger.info("  Step 3: Recording glyph outlines...")
    all_recordings = {}  # final_glyph_name -> (RecordingPen, advanceWidth)
    all_hmtx = {}
    final_cmap = {}
    glyph_order = ['.notdef']
    glyph_counter = 0
    
    for fidx in sorted(font_unicode_map.keys()):
        fpath = font_paths[fidx]
        fname = os.path.basename(fpath)
        uvs = font_unicode_map[fidx]
        logger.info(f"    Font {fidx} ({fname}): recording {len(uvs)} glyphs...")
        
        font = TTFont(fpath)
        gs = font.getGlyphSet()
        hmtx_data = font['hmtx'].metrics
        
        recorded = 0
        for uv in sorted(uvs.keys()):
            orig_gname = uvs[uv]
            # Create unique glyph name to avoid conflicts across fonts
            final_gname = f"g{glyph_counter:05d}"
            glyph_counter += 1
            
            if glyph_counter > MAX_GLYPHS:
                logger.warning(f"  Reached max glyphs ({MAX_GLYPHS}), stopping!")
                break
            
            try:
                rec = RecordingPen()
                gs[orig_gname].draw(rec)
                width = gs[orig_gname].width
                all_recordings[final_gname] = (rec, width)
                all_hmtx[final_gname] = hmtx_data.get(orig_gname, (width, 0))
                final_cmap[uv] = final_gname
                glyph_order.append(final_gname)
                recorded += 1
            except Exception as e:
                logger.debug(f"      Skip {orig_gname}: {e}")
        
        font.close()
        logger.info(f"      Recorded {recorded} glyphs")
        gc.collect()
        
        if glyph_counter > MAX_GLYPHS:
            break
    
    logger.info(f"  Total glyphs: {len(glyph_order)}, cmap entries: {len(final_cmap)}")
    
    # Step 4: Build charstrings
    logger.info("  Step 4: Building CFF charstrings...")
    charstrings = {}
    widths = {}
    
    for i, gname in enumerate(glyph_order):
        if gname in all_recordings:
            rec, width = all_recordings[gname]
            try:
                t2pen = T2CharStringPen(width, None)
                rec.replay(t2pen)
                charstrings[gname] = t2pen.getCharString()
                widths[gname] = all_hmtx.get(gname, (width, 0))
            except Exception:
                t2pen = T2CharStringPen(0, None)
                charstrings[gname] = t2pen.getCharString()
                widths[gname] = (0, 0)
        else:
            t2pen = T2CharStringPen(0, None)
            charstrings[gname] = t2pen.getCharString()
            widths[gname] = (0, 0)
        
        if (i + 1) % 10000 == 0:
            logger.info(f"    {i+1}/{len(glyph_order)} charstrings built...")
    
    # Step 5: Assemble OTF
    logger.info("  Step 5: Assembling OTF font...")
    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(final_cmap)
    fb.setupHorizontalMetrics(widths)
    fb.setupHorizontalHeader()
    fb.setupNameTable({"familyName": font_name, "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.setupHead(unitsPerEm=1000)
    fb.setupCFF(font_name, {}, charstrings, privateDict={})
    fb.save(output_path)
    
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    logger.info(f"  Saved: {output_path} ({size_mb:.1f} MB)")
    return True


def run_workflow():
    logger.info("=" * 60)
    logger.info("NotoSansMundi - Font Merge Workflow")
    logger.info("=" * 60)
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    all_files = get_otf_files(SOURCE_DIR)
    logger.info(f"Found {len(all_files)} OTF files")
    
    cjk_files = [(f, p, s) for f, p, s in all_files if is_cjk(f)]
    non_cjk_files = [(f, p, s) for f, p, s in all_files if not is_cjk(f)]
    logger.info(f"  Non-CJK: {len(non_cjk_files)}, CJK: {len(cjk_files)}")
    
    # Put NotoSans-Regular.otf first, then CJK SC, then rest of CJK, then other non-CJK
    non_cjk_paths = [p for _, p, _ in non_cjk_files]
    cjk_paths = [p for _, p, _ in cjk_files]
    
    # Reorder: base first, then CJK SC, other CJK, then non-CJK by size
    base_idx = next((i for i, (f, _, _) in enumerate(non_cjk_files) if f == "NotoSans-Regular.otf"), None)
    base_path = non_cjk_paths.pop(base_idx) if base_idx is not None else non_cjk_paths.pop(0)
    
    sc_path = [p for p in cjk_paths if "sc" in os.path.basename(p).lower()]
    other_cjk = [p for p in cjk_paths if "sc" not in os.path.basename(p).lower()]
    
    # Final order: NotoSans base -> CJK SC -> other CJK -> non-CJK (small to large)
    ordered_paths = [base_path] + sc_path + other_cjk + non_cjk_paths
    
    # Build single merged font
    success = build_merged_font(ordered_paths, OUTPUT_FILE, "NotoSansMundi")
    
    if success and os.path.exists(OUTPUT_FILE):
        size_mb = os.path.getsize(OUTPUT_FILE) / (1024 * 1024)
        font = TTFont(OUTPUT_FILE)
        logger.info("\n" + "=" * 60)
        logger.info("WORKFLOW COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Output: {OUTPUT_FILE}")
        logger.info(f"Size: {size_mb:.1f} MB")
        logger.info(f"Glyphs: {len(font.getGlyphOrder())}")
        logger.info(f"Cmap entries: {len(font.getBestCmap())}")
        font.close()
        return True
    else:
        logger.error("Failed!")
        return False


if __name__ == "__main__":
    success = run_workflow()
    sys.exit(0 if success else 1)
