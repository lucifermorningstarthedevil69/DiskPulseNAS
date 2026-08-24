"""
Icon generation utility for DiskPulse GUI launchers.
Creates an in-memory or saved PIL Image / ICO for desktop windows and system tray.
"""
from PIL import Image, ImageDraw

def create_diskpulse_icon(size: int = 64) -> Image.Image:
    """Generate a clean dark-neon DiskPulse logo icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Outer rounded circle / squircle background (Dark Navy/Slate)
    margin = int(size * 0.06)
    draw.ellipse([margin, margin, size - margin, size - margin], fill=(15, 23, 42, 255), outline=(56, 189, 248, 255), width=max(1, int(size * 0.05)))
    
    # Pulse waveform / disk lines in cyan/teal
    center_y = size // 2
    points = [
        (int(size * 0.22), center_y),
        (int(size * 0.38), center_y),
        (int(size * 0.46), int(size * 0.26)),
        (int(size * 0.56), int(size * 0.74)),
        (int(size * 0.64), center_y),
        (int(size * 0.78), center_y),
    ]
    draw.line(points, fill=(56, 189, 248, 255), width=max(2, int(size * 0.07)), joint="round")
    
    return img
