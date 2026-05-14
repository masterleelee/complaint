import os
import glob

# 清除 Python 缓存
for pyc in glob.glob("**/__pycache__", recursive=True):
    import shutil
    if os.path.isdir(pyc):
        shutil.rmtree(pyc, ignore_errors=True)
        print(f"清除: {pyc}")

# 清除 .pyc 文件
for pyc in glob.glob("**/*.pyc", recursive=True):
    os.remove(pyc)
    print(f"删除: {pyc}")

print("缓存已清除")
