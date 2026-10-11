#!/usr/bin/env python3

import os
import queue
import tempfile
import threading
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError
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

    temp_pdf = None
    try:
        with Image.open(img_path) as img:
            # 对 GIF：取第一帧（可扩展为多页）
            if getattr(img, "is_animated", False):
                img = ImageSequence.Iterator(img).__next__()  # 第一帧

            # 将 EXIF 方向应用到像素，避免手机照片在 PDF 中方向错误。
            img = ImageOps.exif_transpose(img)

            # Pillow 要求保存为 PDF 前是 RGB 或 L
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                # 透明背景改为白色（可改）
                bg = Image.new("RGB", img.size, (255, 255, 255))
                rgba = img.convert("RGBA")
                bg.paste(rgba, mask=rgba.getchannel("A"))
                rgb = bg
            else:
                rgb = img.convert("RGB")

            # 先写入目标目录的临时文件，完成后再替换，避免失败时留下残缺 PDF。
            output_dir = os.path.dirname(out_pdf) or "."
            fd, temp_pdf = tempfile.mkstemp(prefix=".img2pdf-", suffix=".tmp", dir=output_dir)
            os.close(fd)
            rgb.save(temp_pdf, "PDF", resolution=100.0)

        if overwrite:
            os.replace(temp_pdf, out_pdf)
        elif os.name == "nt":
            # Windows rename fails if the destination appeared after the initial check.
            try:
                os.rename(temp_pdf, out_pdf)
            except FileExistsError:
                return False, f"已存在，跳过: {out_pdf}"
        else:
            # A same-filesystem hard link is atomic and never replaces an existing file.
            try:
                os.link(temp_pdf, out_pdf)
            except FileExistsError:
                return False, f"已存在，跳过: {out_pdf}"
            os.remove(temp_pdf)
        temp_pdf = None
        return True, f"已转换: {out_pdf}"
    except UnidentifiedImageError:
        return False, f"无法识别为图片，跳过: {img_path}"
    except Exception as e:
        return False, f"错误 ({img_path}): {e}"
    finally:
        if temp_pdf and os.path.exists(temp_pdf):
            try:
                os.remove(temp_pdf)
            except OSError:
                pass

def find_images_recursive(root_dir, stop_event=None):
    """递归查找图片，并返回结果及无法读取的目录错误。"""
    results = []
    errors = []

    def onerror(error):
        errors.append(error)

    for dirpath, dirnames, filenames in os.walk(root_dir, onerror=onerror):
        if stop_event and stop_event.is_set():
            break
        for fn in filenames:
            if stop_event and stop_event.is_set():
                break
            if is_image_file(fn):
                results.append(os.path.join(dirpath, fn))
    return results, errors

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
        self._ui_queue = queue.Queue()
        self._closing = False
        master.protocol("WM_DELETE_WINDOW", self.close)
        master.after(50, self._process_ui_queue)

    def choose_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.dir_var.set(d)

    def log_message(self, msg):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _process_ui_queue(self):
        """在 Tk 主线程中处理工作线程发来的日志和状态更新。"""
        while True:
            try:
                item = self._ui_queue.get_nowait()
            except queue.Empty:
                break

            action = item[0]
            if action == "log":
                self.log_message(item[1])
            elif action == "info" and not self._closing:
                messagebox.showinfo(item[1], item[2])
            elif action == "error" and not self._closing:
                messagebox.showerror(item[1], item[2])
            elif action == "finished":
                self.start_btn.configure(state=tk.NORMAL)
                self.stop_btn.configure(state=tk.DISABLED)
                self._worker_thread = None
                if self._closing:
                    self.master.destroy()
                    return

        if not self._closing:
            self.master.after(50, self._process_ui_queue)

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

    def close(self):
        """请求后台任务在当前图片结束后停止，再关闭窗口。"""
        if self._worker_thread and self._worker_thread.is_alive():
            if not self._closing:
                self._closing = True
                self._stop_flag.set()
                self.start_btn.configure(state=tk.DISABLED)
                self.stop_btn.configure(state=tk.DISABLED)
                self.log_message("正在停止任务，当前图片处理完成后关闭窗口...")
            self.master.after(100, self._close_when_worker_stops)
        else:
            self._closing = True
            self.master.destroy()

    def _close_when_worker_stops(self):
        if self._worker_thread and self._worker_thread.is_alive():
            self.master.after(100, self._close_when_worker_stops)
        else:
            self.master.destroy()

    def _worker(self, root_dir):
        try:
            imgs, scan_errors = find_images_recursive(root_dir, self._stop_flag)
            for error in scan_errors:
                self._ui_queue.put(("log", f"读取目录失败，部分内容未扫描: {error}"))

            if self._stop_flag.is_set():
                self._ui_queue.put(("log", "扫描期间任务已被用户中断，未开始转换。"))
                self._ui_queue.put(("info", "已停止", "扫描期间任务已停止，未开始转换。"))
                return

            total = len(imgs)
            self._ui_queue.put(("log", f"共找到 {total} 个图片文件。"))
            if total == 0:
                if scan_errors:
                    summary = f"没有找到图片；目录读取失败: {len(scan_errors)} 处。"
                    self._ui_queue.put(("log", summary))
                    self._ui_queue.put(("info", "完成", summary))
                else:
                    self._ui_queue.put(("log", "没有找到图片，任务结束。"))
                return

            converted = 0
            skipped = 0
            failed = len(scan_errors)
            interrupted = False

            for idx, img_path in enumerate(imgs, start=1):
                if self._stop_flag.is_set():
                    interrupted = True
                    self._ui_queue.put(("log", "任务已被用户中断。"))
                    break

                self._ui_queue.put(("log", f"[{idx}/{total}] 处理: {img_path}"))
                ok, msg = convert_image_to_pdf(img_path, overwrite=OVERWRITE_EXISTING)
                self._ui_queue.put(("log", "  -> " + msg))
                if ok:
                    converted += 1
                else:
                    # 根据消息判断是跳过还是失败（简单分类）
                    if msg.startswith("已存在") or msg.startswith("无法识别为图片") or msg.startswith("跳过"):
                        skipped += 1
                    else:
                        failed += 1

            interrupted = interrupted or self._stop_flag.is_set()
            summary = f"转换成功: {converted}，跳过: {skipped}，失败: {failed}。"
            if interrupted:
                self._ui_queue.put(("log", "任务已停止。"))
                self._ui_queue.put(("info", "已停止", f"任务已停止。\n{summary}"))
            else:
                self._ui_queue.put(("log", "任务完成。"))
                self._ui_queue.put(("info", "完成", f"任务完成。\n{summary}"))
            self._ui_queue.put(("log", summary))
        except Exception as e:
            self._ui_queue.put(("log", f"未处理的错误: {e}"))
            self._ui_queue.put(("error", "错误", f"发生错误：{e}"))
        finally:
            self._ui_queue.put(("finished",))

def main():
    root = tk.Tk()
    app = App(root)
    root.geometry("800x520")
    root.mainloop()

if __name__ == "__main__":
    main()
