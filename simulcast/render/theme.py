BG = (12, 12, 14, 216)
BORDER = (255, 255, 255, 28)
DOT_ON = (255, 255, 255, 235)
DOT_OFF = (74, 74, 78, 220)
DOT_HALO = (255, 255, 255, 46)
TEXT_FINAL = "#FFFFFF"
TEXT_DRAFT = "#E4E4E8"
TEXT_TAIL = "#8A8A90"
SOURCE = "#9A9AA0"
PLACEHOLDER = "#6E6E73"
RADIUS = 26
PAD_L = 58
PAD_R = 34
PAD_T = 20
PAD_B = 22
FONT_STACK = ["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC"]


def apply(data: dict) -> None:
    global BG, BORDER, DOT_ON, DOT_OFF, DOT_HALO, TEXT_FINAL, TEXT_DRAFT, TEXT_TAIL, SOURCE, PLACEHOLDER, RADIUS
    if "bg" in data:
        BG = tuple(int(x) for x in data["bg"])
    if "border" in data:
        BORDER = tuple(int(x) for x in data["border"])
    if "dot_on" in data:
        DOT_ON = tuple(int(x) for x in data["dot_on"])
    if "dot_off" in data:
        DOT_OFF = tuple(int(x) for x in data["dot_off"])
    if "dot_halo" in data:
        DOT_HALO = tuple(int(x) for x in data["dot_halo"])
    if "text_final" in data:
        TEXT_FINAL = str(data["text_final"])
    if "text_draft" in data:
        TEXT_DRAFT = str(data["text_draft"])
    if "text_tail" in data:
        TEXT_TAIL = str(data["text_tail"])
    if "source" in data:
        SOURCE = str(data["source"])
    if "placeholder" in data:
        PLACEHOLDER = str(data["placeholder"])
    if "radius" in data:
        RADIUS = int(data["radius"])
