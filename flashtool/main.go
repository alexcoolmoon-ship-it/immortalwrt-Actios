package main

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"flashtool/fastboot"
	"flashtool/winenv"
)

var Version = "v1.0.1"

const (
	colorReset  = "\033[0m"
	colorGreen  = "\033[32m"
	colorYellow = "\033[33m"
	colorRed    = "\033[31m"
	colorCyan   = "\033[36m"
	colorBold   = "\033[1m"
	colorGray   = "\033[90m"
)

func main() {
	enableANSI()
	printBanner()

	// ── 环境检查 ──────────────────────────────────────────────────
	printSection("正在检查运行环境")
	envErr := winenv.CheckAndInstall(func(msg string, ok bool) {
		if ok {
			fmt.Printf("  %s✓%s %s\n", colorGreen, colorReset, msg)
		} else {
			fmt.Printf("  %s✗%s %s\n", colorRed, colorReset, msg)
		}
		time.Sleep(100 * time.Millisecond)
	})
	if envErr != nil {
		fatal("环境初始化失败：" + envErr.Error())
	}
	fmt.Printf("  %s✓%s 环境就绪\n", colorGreen, colorReset)

	// ── 检查固件文件 ──────────────────────────────────────────────
	printSection("检查固件文件")
	exeDir := getExeDir()
	romDir := filepath.Join(exeDir, "rom")

	if err := checkRomFiles(romDir); err != nil {
		fatal(err.Error())
	}
	bootImg := filepath.Join(romDir, "boot.img")
	systemImg := filepath.Join(romDir, "system.img")
	if _, err := os.Stat(bootImg); os.IsNotExist(err) {
		fatal("缺少 rom/boot.img\n  请从以下地址下载固件后放入 rom 文件夹：\n  https://github.com/x7780/immortalwrt-Actios")
	}
	if _, err := os.Stat(systemImg); os.IsNotExist(err) {
		fatal("缺少 rom/system.img\n  请从以下地址下载固件后放入 rom 文件夹：\n  https://github.com/x7780/immortalwrt-Actios")
	}
	fmt.Printf("  %s✓%s 底层固件：完整\n", colorGreen, colorReset)
	fmt.Printf("  %s✓%s boot.img：就绪\n", colorGreen, colorReset)
	fmt.Printf("  %s✓%s system.img：就绪\n", colorGreen, colorReset)

	const chunkSize = 200 * 1024 * 1024
	if err := fastboot.ValidateSparseFile(systemImg, chunkSize); err != nil {
		fatal("Invalid system.img (no device writes started): " + err.Error())
	}
	backupDir := filepath.Join(exeDir, "backup_"+time.Now().Format("20060102_150405"))

	// ── 步骤 1：等待设备 ──────────────────────────────────────────
	printSection("等待设备连接")
	fmt.Println()
	fmt.Println(colorYellow + colorBold + "  请按以下步骤操作：" + colorReset)
	fmt.Println()
	fmt.Println("    1. 按住设备上的 " + colorBold + "音量-" + colorReset + " 键，不要松开")
	fmt.Println("    2. 将 USB 线插入电脑")
	fmt.Println("    3. 等待程序自动检测（约3秒后可松开按键）")
	fmt.Println()
	fmt.Print(colorGray + "  正在等待设备接入" + colorReset)

	dev, err := fastboot.WaitForDevice(120)
	if err != nil {
		fmt.Println()
		fatal("未检测到设备（等待超时）\n  请确认：USB线已插好，已按住音量-键再插入")
	}
	fmt.Println()
	fmt.Printf("  %s✓%s 检测到 Fastboot 设备\n", colorGreen, colorReset)

	if product, err := dev.GetVar("product"); err == nil {
		fmt.Printf("  %s✓%s 设备型号：%s\n", colorGreen, colorReset, trimOKAY(product))
	}
	if serialno, err := dev.GetVar("serialno"); err == nil {
		fmt.Printf("  %s✓%s 序列号：%s\n", colorGreen, colorReset, trimOKAY(serialno))
	}

	// ── 步骤 2：刷入 lk2nd ────────────────────────────────────────
	printSection("刷入 lk2nd 引导 [1/3]")

	fmt.Printf("  → 擦除 boot 分区...")
	if err := dev.Erase("boot"); err != nil {
		fmt.Printf(" %s跳过%s\n", colorGray, colorReset)
	} else {
		fmt.Printf(" %s完成%s\n", colorGreen, colorReset)
	}

	fmt.Printf("  → 刷入 lk2nd.img\n")
	if err := dev.FlashFile("boot", filepath.Join(romDir, "lk2nd.img"), makeProgress("lk2nd.img")); err != nil {
		fatal("刷入 lk2nd 失败：" + err.Error())
	}

	fmt.Printf("  → 重启到 Bootloader...")
	dev.RebootBootloader()
	dev.Close()
	fmt.Printf(" %s完成%s\n", colorGreen, colorReset)

	fmt.Print(colorGray + "  等待设备重新就绪" + colorReset)
	dev, err = fastboot.WaitForDevice(30)
	if err != nil {
		fatal("设备重启后未检测到，请重试")
	}
	fmt.Println()
	fmt.Printf("  %s✓%s lk2nd 刷入完成\n", colorGreen, colorReset)

	// ── 步骤 3：备份基带 + 刷底层固件 ────────────────────────────
	printSection("备份基带 + 刷入底层固件 [2/3]")

	if err := os.MkdirAll(backupDir, 0755); err != nil {
		fatal("无法创建备份目录：" + err.Error())
	}

	partitions := []string{"fsc", "fsg", "modemst1", "modemst2"}
	if err := backupNV(dev, partitions, backupDir); err != nil {
		fatal(err.Error())
	}
	fmt.Printf("  %s✓%s 基带备份完成 → %s\n", colorGreen, colorReset, filepath.Base(backupDir))

	fmt.Printf("  → 擦除 lk2nd / boot...")
	dev.Erase("lk2nd")
	dev.Erase("boot")
	dev.RebootBootloader()
	dev.Close()
	fmt.Printf(" %s完成%s\n", colorGreen, colorReset)

	fmt.Print(colorGray + "  等待设备重新就绪" + colorReset)
	dev, err = fastboot.WaitForDevice(30)
	if err != nil {
		fatal("设备重启后未检测到，请重试")
	}
	fmt.Println()

	flashFiles := []struct{ partition, file string }{
		{"partition", "gpt_both0.bin"},
		{"hyp", "hyp.mbn"},
		{"rpm", "rpm.mbn"},
		{"sbl1", "sbl1.mbn"},
		{"tz", "tz.mbn"},
		{"aboot", "aboot.bin"},
		{"cdt", "sbc_1.0_8016.bin"},
	}
	for _, f := range flashFiles {
		fmt.Printf("  → 刷入 %s\n", f.file)
		if err := dev.FlashFile(f.partition, filepath.Join(romDir, f.file), makeProgress(f.file)); err != nil {
			fatal(fmt.Sprintf("刷入 %s 失败：%s", f.file, err.Error()))
		}
	}

	// Restore only a complete, validated set; abort on the first failure.
	if err := restoreNV(dev, partitions, backupDir); err != nil {
		fatal(err.Error())
	}

	dev.Erase("boot")
	dev.Erase("rootfs")
	dev.RebootBootloader()
	dev.Close()

	fmt.Print(colorGray + "  等待设备重新就绪" + colorReset)
	dev, err = fastboot.WaitForDevice(30)
	if err != nil {
		fatal("设备重启后未检测到，请重试")
	}
	fmt.Println()
	fmt.Printf("  %s✓%s 底层固件刷入完成\n", colorGreen, colorReset)

	// ── 步骤 4：刷入系统镜像 ─────────────────────────────────────
	printSection("刷入系统镜像 [3/3]")

	fmt.Printf("  → 刷入 boot.img\n")
	if err := dev.FlashFile("boot", bootImg, makeProgress("boot.img")); err != nil {
		fatal("刷入 boot.img 失败：" + err.Error())
	}

	fmt.Printf("  → 刷入 system.img（较大，请耐心等待）\n")
	if err := dev.FlashFileSparse("rootfs", systemImg, chunkSize, makeProgress("system.img")); err != nil {
		fatal("刷入 system.img 失败：" + err.Error())
	}

	fmt.Printf("  → 重启设备...")
	dev.Reboot()
	dev.Close()
	fmt.Printf(" %s完成%s\n", colorGreen, colorReset)

	// ── 完成 ─────────────────────────────────────────────────────
	fmt.Println()
	fmt.Println(colorGreen + colorBold + "  ╔══════════════════════════════════════════╗" + colorReset)
	fmt.Println(colorGreen + colorBold + "  ║          🎉  刷机完成！                  ║" + colorReset)
	fmt.Println(colorGreen + "  ║  设备正在重启，请等待约30秒...           ║" + colorReset)
	fmt.Println(colorGreen + "  ║  基带备份已保存到：                      ║" + colorReset)
	fmt.Printf(colorGreen+"  ║  %-42s║\n"+colorReset, "  "+filepath.Base(backupDir)+"/")
	fmt.Println(colorGreen + colorBold + "  ╚══════════════════════════════════════════╝" + colorReset)
	fmt.Println()
	pressAnyKey("按回车键退出...")
}

// ── 辅助函数 ──────────────────────────────────────────────────────────────────

func printBanner() {
	fmt.Println(colorCyan + colorBold)
	fmt.Println("  ╔══════════════════════════════════════════╗")
	fmt.Println("  ║     OpenStick 傻瓜一键刷机工具           ║")
	fmt.Println("  ║     支持高通410系列随身WiFi               ║")
	fmt.Printf("  ║     版本：%-32s║\n", Version)
	fmt.Println("  ╚══════════════════════════════════════════╝")
	fmt.Println(colorReset)
	fmt.Println("  固件下载：https://github.com/x7780/immortalwrt-Actios")
	fmt.Println()
}

func printSection(title string) {
	fmt.Printf("\n%s━━ %s %s\n", colorYellow+colorBold, title, colorReset)
}

func fatal(msg string) {
	fmt.Println()
	fmt.Println(colorRed+colorBold+"  ✗ 错误："+colorReset, msg)
	fmt.Println()
	pressAnyKey("按回车键退出...")
	os.Exit(1)
}

func makeProgress(name string) func(sent, total int) {
	return func(sent, total int) {
		if total == 0 {
			return
		}
		pct := sent * 100 / total
		filled := pct / 5
		if filled > 20 {
			filled = 20
		}
		bar := strings.Repeat("█", filled) + strings.Repeat("░", 20-filled)
		fmt.Printf("\r     [%s] %3d%%  %s/%s    ",
			bar, pct, humanSize(sent), humanSize(total))
		if sent >= total {
			fmt.Println()
		}
	}
}

func humanSize(b int) string {
	switch {
	case b >= 1024*1024:
		return fmt.Sprintf("%.1fMB", float64(b)/1024/1024)
	case b >= 1024:
		return fmt.Sprintf("%.1fKB", float64(b)/1024)
	default:
		return fmt.Sprintf("%dB", b)
	}
}

func checkRomFiles(romDir string) error {
	required := []string{
		"lk2nd.img", "gpt_both0.bin", "hyp.mbn",
		"rpm.mbn", "sbl1.mbn", "tz.mbn",
		"aboot.bin", "sbc_1.0_8016.bin",
	}
	var missing []string
	for _, f := range required {
		if _, err := os.Stat(filepath.Join(romDir, f)); os.IsNotExist(err) {
			missing = append(missing, f)
		}
	}
	if len(missing) > 0 {
		return fmt.Errorf("rom/ 目录缺少底层固件：\n     %s", strings.Join(missing, "\n     "))
	}
	return nil
}

func getExeDir() string {
	exe, err := os.Executable()
	if err != nil {
		return "."
	}
	return filepath.Dir(exe)
}

func pressAnyKey(prompt string) {
	fmt.Print(prompt)
	reader := bufio.NewReader(os.Stdin)
	_, _ = reader.ReadString('\n')
}

func enableANSI() {
	if runtime.GOOS != "windows" {
		return
	}
	_ = os.Setenv("TERM", "xterm-256color")
}

// trimOKAY 去掉 Fastboot 响应头的 "OKAY" 前缀
func trimOKAY(s string) string {
	return strings.TrimSpace(strings.TrimPrefix(s, "OKAY"))
}
