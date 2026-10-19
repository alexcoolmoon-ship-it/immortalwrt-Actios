// Package fastboot 实现 Fastboot USB 协议，直接通过 libusb 通信，无需安装任何驱动。
package fastboot

import (
	"fmt"
	"io"
	"math/bits"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/google/gousb"
)

const (
	// Fastboot 设备的 USB Class/SubClass/Protocol
	fbClass    = 0xff
	fbSubClass = 0x42
	fbProtocol = 0x03

	maxPacket = 64 * 1024 // 64KB 单次传输上限
	timeout   = 30 * time.Second
)

// Device 代表一个已连接的 Fastboot 设备
type Device struct {
	ctx   *gousb.Context
	dev   *gousb.Device
	cfg   *gousb.Config
	iface *gousb.Interface
	inEP  io.Reader
	outEP io.Writer
}

// Open 扫描 USB 总线，找到第一个 Fastboot 设备并打开
func Open() (*Device, error) {
	ctx := gousb.NewContext()

	devs, err := ctx.OpenDevices(func(desc *gousb.DeviceDesc) bool {
		for _, cfg := range desc.Configs {
			for _, iface := range cfg.Interfaces {
				for _, alt := range iface.AltSettings {
					if alt.Class == fbClass &&
						alt.SubClass == fbSubClass &&
						alt.Protocol == fbProtocol {
						return true
					}
				}
			}
		}
		return false
	})
	// OpenDevices 在没有权限访问某些设备时会返回 err，但只要找到了目标设备就继续
	if len(devs) == 0 {
		ctx.Close()
		if err != nil {
			return nil, fmt.Errorf("未找到 Fastboot 设备：%w", err)
		}
		return nil, fmt.Errorf("未找到 Fastboot 设备，请确认设备已进入 Fastboot 模式")
	}

	usbDev := devs[0]
	// 关闭多余设备
	for _, d := range devs[1:] {
		d.Close()
	}

	usbDev.SetAutoDetach(true)

	// 找到正确的配置和接口
	cfg, err := usbDev.Config(1)
	if err != nil {
		usbDev.Close()
		ctx.Close()
		return nil, fmt.Errorf("无法获取 USB 配置：%w", err)
	}

	var iface *gousb.Interface
	var inEP *gousb.InEndpoint
	var outEP *gousb.OutEndpoint

	for _, ifaceDesc := range cfg.Desc.Interfaces {
		for _, alt := range ifaceDesc.AltSettings {
			if alt.Class == fbClass && alt.SubClass == fbSubClass && alt.Protocol == fbProtocol {
				iface, err = cfg.Interface(ifaceDesc.Number, alt.Alternate)
				if err != nil {
					continue
				}
				// 找 IN 和 OUT endpoint
				for _, ep := range alt.Endpoints {
					if ep.Direction == gousb.EndpointDirectionIn {
						inEP, err = iface.InEndpoint(ep.Number)
						if err != nil {
							iface.Close()
							cfg.Close()
							usbDev.Close()
							ctx.Close()
							return nil, fmt.Errorf("无法打开 IN endpoint：%w", err)
						}
					} else {
						outEP, err = iface.OutEndpoint(ep.Number)
						if err != nil {
							iface.Close()
							cfg.Close()
							usbDev.Close()
							ctx.Close()
							return nil, fmt.Errorf("无法打开 OUT endpoint：%w", err)
						}
					}
				}
				if inEP == nil || outEP == nil {
					iface.Close()
					continue
				}
				goto found
			}
		}
	}
	cfg.Close()
	usbDev.Close()
	ctx.Close()
	return nil, fmt.Errorf("未找到 Fastboot 接口（IN/OUT endpoint 不完整）")

found:
	return &Device{
		ctx:   ctx,
		dev:   usbDev,
		cfg:   cfg,
		iface: iface,
		inEP:  inEP,
		outEP: outEP,
	}, nil
}

// Close 释放设备资源
func (d *Device) Close() {
	if d.iface != nil {
		d.iface.Close()
		d.iface = nil
	}
	if d.cfg != nil {
		d.cfg.Close()
		d.cfg = nil
	}
	if d.dev != nil {
		d.dev.Close()
		d.dev = nil
	}
	if d.ctx != nil {
		d.ctx.Close()
		d.ctx = nil
	}
}

// Command 发送一条 Fastboot 命令，返回设备响应
func (d *Device) Command(cmd string) (string, error) {
	if err := d.send([]byte(cmd)); err != nil {
		return "", fmt.Errorf("发送命令失败：%w", err)
	}
	return d.readResponse()
}

// GetVar 获取设备变量
func (d *Device) GetVar(variable string) (string, error) {
	return d.Command("getvar:" + variable)
}

// Erase 擦除分区
func (d *Device) Erase(partition string) error {
	resp, err := d.Command("erase:" + partition)
	if err != nil {
		return err
	}
	if !strings.HasPrefix(resp, "OKAY") {
		return fmt.Errorf("擦除 %s 失败：%s", partition, resp)
	}
	return nil
}

// Flash 刷写分区，支持进度回调
func (d *Device) Flash(partition string, data []byte, progress func(sent, total int)) error {
	if len(data) == 0 || uint64(len(data)) > uint64(^uint32(0)) {
		return fmt.Errorf("invalid fastboot download size: %d", len(data))
	}
	// 1. 告诉设备准备接收多少字节
	resp, err := d.Command(fmt.Sprintf("download:%08x", len(data)))
	if err != nil {
		return err
	}
	size, sizeErr := dataResponseSize(resp)
	if sizeErr != nil || size != uint32(len(data)) {
		return fmt.Errorf("download size mismatch: %q", resp)
	}

	// 2. 分块发送数据
	total := len(data)
	sent := 0
	for sent < total {
		end := sent + maxPacket
		if end > total {
			end = total
		}
		n, err := d.outEP.Write(data[sent:end])
		if err != nil {
			return fmt.Errorf("数据传输失败（offset %d）：%w", sent, err)
		}
		if n <= 0 || n > end-sent {
			return fmt.Errorf("data transfer made no progress: %w", io.ErrShortWrite)
		}
		sent += n
		if progress != nil {
			progress(sent, total)
		}
	}

	// 3. 等待 OKAY
	resp, err = d.readResponse()
	if err != nil {
		return err
	}
	if !strings.HasPrefix(resp, "OKAY") {
		return fmt.Errorf("download 完成确认失败：%s", resp)
	}

	// 4. 发送 flash 命令
	resp, err = d.Command("flash:" + partition)
	if err != nil {
		return err
	}
	if !strings.HasPrefix(resp, "OKAY") {
		return fmt.Errorf("flash %s 失败：%s", partition, resp)
	}
	return nil
}

// FlashFile 从文件刷写分区
func (d *Device) FlashFile(partition, path string, progress func(sent, total int)) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("读取文件失败 %s：%w", path, err)
	}
	return d.Flash(partition, data, progress)
}

// FlashFileSparse sends valid Android sparse fragments with explicit block
// offsets. Repeated raw flash commands would overwrite the partition's start.
func (d *Device) FlashFileSparse(partition, path string, chunkSize int, progress func(sent, total int)) error {
	f, err := os.Open(path)
	if err != nil {
		return fmt.Errorf("open image %s: %w", path, err)
	}
	defer f.Close()
	plan, err := readSparsePlan(f, chunkSize)
	if err != nil {
		return err
	}
	err = plan.flash(f, chunkSize, func(fragment []byte, start, length int64) error {
		return d.Flash(partition, fragment, func(sent, total int) {
			if progress != nil {
				high, low := bits.Mul64(uint64(sent), uint64(length))
				done, _ := bits.Div64(high, low, uint64(total))
				progress(int(start+int64(done)), int(plan.byteSize()))
			}
		})
	})
	if err == nil && progress != nil {
		progress(int(plan.byteSize()), int(plan.byteSize()))
	}
	return err
}

// OEMDump publishes a private backup only after the declared number of bytes
// and the final OKAY have arrived. get_staged is a CLI name; its wire command
// is upload, as in the AOSP Fastboot driver.
func (d *Device) OEMDump(partition, outFile string) error {
	lock, err := os.OpenFile(outFile+".lock", os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer func() { lock.Close(); os.Remove(outFile + ".lock") }()
	if _, err := os.Lstat(outFile); !os.IsNotExist(err) {
		return fmt.Errorf("backup destination already exists or is inaccessible: %s", outFile)
	}
	tmp, err := os.CreateTemp(filepath.Dir(outFile), "."+filepath.Base(outFile)+".*.partial")
	if err != nil {
		return err
	}
	defer func() { tmp.Close(); os.Remove(tmp.Name()) }()
	resp, err := d.Command("oem dump " + partition)
	if err != nil {
		return fmt.Errorf("oem dump %s: %w", partition, err)
	}
	if !strings.HasPrefix(resp, "OKAY") {
		return fmt.Errorf("oem dump not acknowledged: %q", resp)
	}
	resp, err = d.Command("upload")
	if err != nil {
		return fmt.Errorf("upload header: %w", err)
	}
	size, err := dataResponseSize(resp)
	if err != nil || size == 0 {
		return fmt.Errorf("invalid upload size response: %q", resp)
	}
	buf := make([]byte, maxPacket)
	var received uint64
	for received < uint64(size) {
		limit := uint64(len(buf))
		if left := uint64(size) - received; left < limit {
			limit = left
		}
		n, readErr := d.inEP.Read(buf[:int(limit)])
		if n < 0 || n > int(limit) {
			return fmt.Errorf("invalid upload read count: %d", n)
		}
		if n > 0 {
			written, writeErr := tmp.Write(buf[:n])
			if writeErr != nil {
				return writeErr
			}
			if written != n {
				return io.ErrShortWrite
			}
			received += uint64(n)
		}
		if readErr != nil {
			return fmt.Errorf("upload interrupted after %d/%d bytes: %w", received, size, readErr)
		}
		if n == 0 {
			return io.ErrNoProgress
		}
	}
	resp, err = d.readResponse()
	if err != nil {
		return fmt.Errorf("upload completion: %w", err)
	}
	if !strings.HasPrefix(resp, "OKAY") {
		return fmt.Errorf("upload not acknowledged: %q", resp)
	}
	if err := tmp.Sync(); err != nil {
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if _, err := os.Lstat(outFile); !os.IsNotExist(err) {
		return fmt.Errorf("backup destination appeared during upload: %s", outFile)
	}
	return os.Rename(tmp.Name(), outFile)
}

func dataResponseSize(resp string) (uint32, error) {
	if len(resp) != 12 || !strings.HasPrefix(resp, "DATA") {
		return 0, fmt.Errorf("invalid DATA response: %q", resp)
	}
	size, err := strconv.ParseUint(resp[4:], 16, 32)
	return uint32(size), err
}

// Reboot 重启设备
func (d *Device) Reboot() error {
	// reboot 命令设备可能不回 OKAY 就断开，忽略错误
	_ = d.send([]byte("reboot"))
	return nil
}

// RebootBootloader 重启到 Bootloader
func (d *Device) RebootBootloader() error {
	// 同上，设备重启后连接断开属正常
	_ = d.send([]byte("reboot-bootloader"))
	return nil
}

// ── 内部方法 ──────────────────────────────────────────────────────────────────

func (d *Device) send(data []byte) error {
	n, err := d.outEP.Write(data)
	if err == nil && n != len(data) {
		return io.ErrShortWrite
	}
	return err
}

// readResponse 读取设备响应，处理 INFO 多行和最终 OKAY/FAIL
// Fastboot 响应格式固定为 4字节类型 + 内容，每包最大 64 字节
func (d *Device) readResponse() (string, error) {
	deadline := time.Now().Add(timeout)

	for time.Now().Before(deadline) {
		buf := make([]byte, 64)
		n, err := d.inEP.Read(buf)
		if err != nil {
			return "", fmt.Errorf("读取响应失败：%w", err)
		}
		if n < 4 {
			return "", fmt.Errorf("short fastboot response: %d bytes", n)
		}
		resp := string(buf[:n])
		tag := resp[:4]

		switch tag {
		case "OKAY":
			return resp, nil
		case "FAIL":
			return resp, fmt.Errorf("设备返回错误：%s", resp[4:])
		case "DATA":
			return resp, nil
		case "INFO":
			// INFO 是设备打印的日志，继续读直到 OKAY/FAIL
			continue
		default:
			return "", fmt.Errorf("unknown fastboot response: %q", resp)
		}
	}
	return "", fmt.Errorf("等待响应超时（%v）", timeout)
}

// WaitForDevice 等待 Fastboot 设备出现，最多等 waitSec 秒
func WaitForDevice(waitSec int) (*Device, error) {
	deadline := time.Now().Add(time.Duration(waitSec) * time.Second)
	for time.Now().Before(deadline) {
		dev, err := Open()
		if err == nil {
			return dev, nil
		}
		time.Sleep(500 * time.Millisecond)
	}
	return nil, fmt.Errorf("等待设备超时（%d秒）", waitSec)
}

// SparseImageSize returns the validated logical image size. blk_sz is at
// offset 12 in the Android sparse header, not offset 8 (the header sizes).
func SparseImageSize(path string) (int64, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	plan, err := readSparsePlan(f, 200*1024*1024)
	if err != nil {
		return 0, err
	}
	return plan.byteSize(), nil
}
