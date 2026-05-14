"""图片压缩服务 - 针对合同/文档图片优化"""
import os
from PIL import Image
import io

# 压缩参数配置
COMPRESS_CONFIG = {
    # 最大尺寸限制（宽度, 高度）- 足够看清合同文字
    "max_size": (1920, 1080),
    # JPEG 质量（1-100），85 是视觉无损的甜点
    "jpeg_quality": 85,
    # PNG 压缩级别
    "png_compress": 6,
    # 目标文件大小（MB），超过则进一步压缩
    "target_max_mb": 2.0,
}


def compress_image(input_path: str, output_path: str = None, **kwargs) -> str:
    """
    智能压缩图片，针对合同/文档优化
    
    Args:
        input_path: 原始图片路径
        output_path: 输出路径（默认覆盖原文件）
        **kwargs: 覆盖默认配置
    
    Returns:
        压缩后的文件路径
    """
    config = {**COMPRESS_CONFIG, **kwargs}
    
    if output_path is None:
        output_path = input_path
    
    # 获取原始文件大小
    original_size = os.path.getsize(input_path)
    
    with Image.open(input_path) as img:
        # 记录原始尺寸
        original_width, original_height = img.size
        
        # 转换为 RGB（去除透明通道）
        if img.mode in ('RGBA', 'P'):
            # 白色背景替换透明
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'RGBA':
                background.paste(img, mask=img.split()[3])  # 使用 alpha 通道
            else:
                background.paste(img)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # 等比缩放（保持长宽比）
        max_width, max_height = config["max_size"]
        if original_width > max_width or original_height > max_height:
            img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            print(f"[COMPRESS] Resized: {original_width}x{original_height} -> {img.size}")
        
        # 根据目标文件大小动态调整质量
        quality = config["jpeg_quality"]
        target_bytes = config["target_max_mb"] * 1024 * 1024
        
        # 如果文件太大，逐步降低质量
        while quality >= 60:
            buffer = io.BytesIO()
            img.save(buffer, 'JPEG', quality=quality, optimize=True)
            compressed_bytes = buffer.tell()
            
            if compressed_bytes <= target_bytes or quality <= 60:
                break
            
            quality -= 5
            print(f"[COMPRESS] Quality reduced to {quality} (size: {compressed_bytes/1024/1024:.2f}MB)")
        
        # 保存最终文件
        with open(output_path, 'wb') as f:
            f.write(buffer.getvalue())
        
        # 输出压缩统计
        compressed_size = os.path.getsize(output_path)
        ratio = (1 - compressed_size / original_size) * 100
        print(f"[COMPRESS] {os.path.basename(input_path)}: "
              f"{original_size/1024:.1f}KB -> {compressed_size/1024:.1f}KB "
              f"(-{ratio:.1f}%, quality={quality})")
        
        return output_path


def compress_for_vision_api(image_paths: list, **kwargs) -> list:
    """
    批量压缩图片，用于 Vision API 调用或 PDF 合并
    
    Args:
        image_paths: 图片路径列表
        **kwargs: 压缩参数（可覆盖默认配置）
    
    Returns:
        压缩后的图片路径列表（可能复用原路径）
    """
    # 使用传入的参数覆盖默认配置
    config = {**COMPRESS_CONFIG, **kwargs}
    
    compressed_paths = []
    total_original = 0
    total_compressed = 0
    
    for path in image_paths:
        if not os.path.exists(path):
            compressed_paths.append(path)
            continue
        
        original_size = os.path.getsize(path)
        total_original += original_size
        
        # 如果文件已经很小，跳过压缩
        if original_size < 500 * 1024:  # < 500KB
            compressed_paths.append(path)
            total_compressed += original_size
            continue
        
        # 压缩并保存到临时目录
        temp_dir = os.path.join(os.path.dirname(path), ".compressed")
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, os.path.basename(path))
        
        try:
            compress_image(path, temp_path, **config)
            compressed_paths.append(temp_path)
            total_compressed += os.path.getsize(temp_path)
        except Exception as e:
            print(f"[COMPRESS ERROR] {path}: {e}")
            compressed_paths.append(path)
            total_compressed += original_size
    
    if total_original > 0:
        ratio = (1 - total_compressed / total_original) * 100
        print(f"[COMPRESS TOTAL] {len(image_paths)} files: "
              f"{total_original/1024/1024:.2f}MB -> {total_compressed/1024/1024:.2f}MB "
              f"(-{ratio:.1f}%)")
    
    return compressed_paths


def cleanup_compressed_cache(base_dir: str, max_age_hours: int = 24):
    """清理临时压缩文件"""
    import time
    from pathlib import Path
    
    now = time.time()
    compressed_dirs = list(Path(base_dir).rglob(".compressed"))
    
    for comp_dir in compressed_dirs:
        try:
            for f in comp_dir.iterdir():
                if f.is_file() and (now - f.stat().st_mtime) > max_age_hours * 3600:
                    f.unlink()
            # 如果目录为空，删除目录
            if not any(comp_dir.iterdir()):
                comp_dir.rmdir()
        except Exception as e:
            print(f"[CLEANUP ERROR] {comp_dir}: {e}")
