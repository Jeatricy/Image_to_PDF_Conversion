#!/usr/bin/env python3

import os
import sys
import threading
from PIL import Image, ImageSequence, UnidentifiedImageError
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

# 支持的图片扩展名（小写）
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'}

# 若目标 PDF 已存在，默认不覆盖
OVERWRITE_EXISTING = False

def is_image_file(filename):
    _, ext = os.path.splitext(filename)
    return ext.lower() in IMAGE_EXTS

def convert_image_to_pdf(img_path, overwrite=False):

    base, _ = os.path.splitext(img_path)
    out_pdf = base + ".pdf"

    if os.path.exists(out_pdf) and not overwrite:
        return False, f"已存在，跳过: {out_pdf}"

    try:
        with Image.open(img_path) as img:
            # 对 GIF：取第一帧（可扩展为多页）
            if getattr(img, "is_animated", False):
                img = ImageSequence.Iterator(img).__next__()  # 第一帧

            # Pillow 要求保存为 PDF 前是 RGB 或 L
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                # 透明背景改为白色（可改）
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
                rgb = bg
            else:
                rgb = img.convert("RGB")

            # 保存为 PDF（single page）
            rgb.save(out_pdf, "PDF", resolution=100.0)
        return True, f"已转换: {out_pdf}"
    except UnidentifiedImageError:
        return False, f"无法识别为图片，跳过: {img_path}"
    except Exception as e:
        return False, f"错误 ({img_path}): {e}"

def find_images_recursive(root_dir):
    """递归查找图片文件（返回绝对路径列表）"""
    results = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        for fn in filenames:
            if is_image_file(fn):
                results.append(os.path.join(dirpath, fn))
    return results

# GUI 部分
class App:
    def __init__(self, master):
        self.master = master
        master.title("图片转PDF工具 ")

        frm = tk.Frame(master, padx=10, pady=10)
        frm.pack(fill=tk.BOTH, expand=True)

        lbl = tk.Label(frm, text="选择待处理根目录：")
        lbl.grid(row=0, column=0, sticky='w')

        self.dir_var = tk.StringVar()
        self.dir_entry = tk.Entry(frm, textvariable=self.dir_var, width=60)
        self.dir_entry.grid(row=1, column=0, sticky='we', padx=(0,8))
        btn_browse = tk.Button(frm, text="选择目录", command=self.choose_dir)
        btn_browse.grid(row=1, column=1)

        self.start_btn = tk.Button(frm, text="开始转换", command=self.start_conversion)
        self.start_btn.grid(row=2, column=0, pady=(8,0), sticky='w')

        self.stop_btn = tk.Button(frm, text="停止", command=self.stop_conversion, state=tk.DISABLED)
        self.stop_btn.grid(row=2, column=1, pady=(8,0), sticky='e')

        # 日志输出（可滚动）
        self.log = scrolledtext.ScrolledText(frm, height=20, width=80, state=tk.DISABLED)
        self.log.grid(row=3, column=0, columnspan=2, pady=(10,0), sticky='nsew')

        frm.grid_columnconfigure(0, weight=1)
        frm.grid_rowconfigure(3, weight=1)

        # 控制变量
        self._stop_flag = threading.Event()
        self._worker_thread = None

    def choose_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.dir_var.set(d)

    def log_message(self, msg):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def start_conversion(self):
        root_dir = self.dir_var.get().strip()
        if not root_dir or not os.path.isdir(root_dir):
            messagebox.showwarning("未选择目录", "请先选择一个有效的目录。")
            return

        # 禁用按钮，启动线程
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self._stop_flag.clear()
        self.log_message(f"开始在目录中查找图片：{root_dir}")
        self._worker_thread = threading.Thread(target=self._worker, args=(root_dir,), daemon=True)
        self._worker_thread.start()

    def stop_conversion(self):
        if messagebox.askyesno("确认", "确定停止当前转换任务吗？"):
            self._stop_flag.set()
            self.log_message("已请求停止，正在等待当前操作完成...")

    def _worker(self, root_dir):
        try:
            imgs = find_images_recursive(root_dir)
            total = len(imgs)
            self.log_message(f"共找到 {total} 个图片文件。")
            if total == 0:
                self.log_message("没有找到图片，任务结束。")
                return

            converted = 0
            skipped = 0
            failed = 0

            for idx, img_path in enumerate(imgs, start=1):
                if self._stop_flag.is_set():
                    self.log_message("任务已被用户中断。")
                    break

                self.log_message(f"[{idx}/{total}] 处理: {img_path}")
                ok, msg = convert_image_to_pdf(img_path, overwrite=OVERWRITE_EXISTING)
                self.log_message("  -> " + msg)
                if ok:
                    converted += 1
                else:
                    # 根据消息判断是跳过还是失败（简单分类）
                    if msg.startswith("已存在") or msg.startswith("无法识别为图片") or msg.startswith("跳过"):
                        skipped += 1
                    else:
                        failed += 1

            self.log_message("任务完成。")
            self.log_message(f"转换成功: {converted}，跳过: {skipped}，失败: {failed}。")
            messagebox.showinfo("完成", f"任务完成。\n转换成功: {converted}，跳过: {skipped}，失败: {failed}。")
        except Exception as e:
            self.log_message(f"未处理的错误: {e}")
            messagebox.showerror("错误", f"发生错误：{e}")
        finally:
            self.start_btn.configure(state=tk.NORMAL)
            self.stop_btn.configure(state=tk.DISABLED)
            self._stop_flag.clear()
            self._worker_thread = None

def main():
    root = tk.Tk()
    app = App(root)
    root.geometry("800x520")
    root.mainloop()

if __name__ == "__main__":
    main()
