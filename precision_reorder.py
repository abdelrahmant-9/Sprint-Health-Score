import os

def reorder():
    target = 'dashboard_ui.py'
    try:
        with open(target, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading {target}: {e}")
        return

    # 1. Identify the block to move
    # We look for 'class SprintState:', '_parse_date_str', and '_parse_sprint_date'
    # These were appended at the end.
    
    move_block = []
    normal_lines = []
    
    in_move_block = False
    
    for line in lines:
        if line.startswith('class SprintState:') or \
           line.startswith('def _parse_date_str') or \
           line.startswith('def _parse_sprint_date'):
            in_move_block = True
            move_block.append(line)
        elif in_move_block:
            # Continue capture as long as line is indented or empty
            if line.strip() == '' or line.startswith(' '):
                move_block.append(line)
            else:
                # Next top level function or class reached
                in_move_block = False
                normal_lines.append(line)
        else:
            normal_lines.append(line)

    # 2. Identify insertion point (after imports)
    insertion_idx = 0
    for i, line in enumerate(normal_lines):
        if line.startswith('import ') or line.startswith('from '):
            insertion_idx = i + 1
        elif line.strip() == '':
            continue
        else:
            # First non-import, non-empty line
            break

    # 3. Assemble final content
    final_lines = normal_lines[:insertion_idx] + ["\n"] + move_block + ["\n"] + normal_lines[insertion_idx:]
    
    # 4. Write back
    with open(target, 'w', encoding='utf-8') as f:
        f.writelines(final_lines)

    print(f"Successfully re-ordered {target}. Moved {len(move_block)} lines to the top.")

if __name__ == "__main__":
    reorder()
