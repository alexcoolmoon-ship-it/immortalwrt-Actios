package fastboot

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"hash/crc32"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type responsePacket struct {
	data []byte
	err  error
}
type scriptedUSB struct {
	packets []responsePacket
	writes  [][]byte
	short   bool
}

func (s *scriptedUSB) Read(p []byte) (int, error) {
	if len(s.packets) == 0 {
		return 0, io.EOF
	}
	packet := &s.packets[0]
	n := copy(p, packet.data)
	if n < len(packet.data) {
		packet.data = packet.data[n:]
		return n, nil
	}
	err := packet.err
	s.packets = s.packets[1:]
	return n, err
}
func (s *scriptedUSB) Write(p []byte) (int, error) {
	s.writes = append(s.writes, append([]byte(nil), p...))
	if s.short {
		return 0, nil
	}
	return len(p), nil
}
func packet(s string) responsePacket { return responsePacket{data: []byte(s)} }

func TestOEMDumpPublishesOnlyAcknowledgedCompletePrivateBackup(t *testing.T) {
	data := bytes.Repeat([]byte("NV"), 40000)
	usb := &scriptedUSB{packets: []responsePacket{
		packet("OKAY"), packet(fmt.Sprintf("DATA%08x", len(data))),
		{data: data}, packet("OKAY")}}
	device := &Device{inEP: usb, outEP: usb}
	path := filepath.Join(t.TempDir(), "modemst1.bin")
	if err := device.OEMDump("modemst1", path); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(got, data) {
		t.Fatalf("backup content: %v", err)
	}
	info, _ := os.Stat(path)
	if info.Mode().Perm() != 0600 {
		t.Fatalf("private mode: %o", info.Mode().Perm())
	}
	if len(usb.writes) != 2 || string(usb.writes[0]) != "oem dump modemst1" || string(usb.writes[1]) != "upload" {
		t.Fatalf("wrong wire commands: %q", usb.writes)
	}
	matches, _ := filepath.Glob(filepath.Join(filepath.Dir(path), ".*.partial"))
	if len(matches) != 0 {
		t.Fatal(matches)
	}
	if _, err := os.Stat(path + ".lock"); !os.IsNotExist(err) {
		t.Fatal("stale lock")
	}
}

func TestOEMDumpRejectsInterruptionsBadSizesAndMissingCompletion(t *testing.T) {
	cases := map[string][]responsePacket{
		"early EOF":            {packet("OKAY"), packet("DATA00000008"), {data: []byte("abc"), err: io.EOF}},
		"disconnect":           {packet("OKAY"), packet("DATA00000008"), {data: []byte("abc"), err: errors.New("USB disconnected")}},
		"no progress":          {packet("OKAY"), packet("DATA00000008"), {}},
		"bad final ACK":        {packet("OKAY"), packet("DATA00000008"), packet("abcdefgh"), packet("FAILchecksum")},
		"wrong final response": {packet("OKAY"), packet("DATA00000008"), packet("abcdefgh"), packet("DATA00000000")},
		"missing final ACK":    {packet("OKAY"), packet("DATA00000008"), packet("abcdefgh")},
		"zero size":            {packet("OKAY"), packet("DATA00000000")},
		"bad hex":              {packet("OKAY"), packet("DATAxxxxxxxx")},
		"short header":         {packet("OKAY"), packet("DATA08")},
		"unacknowledged dump":  {packet("DATA00000008")},
	}
	for name, packets := range cases {
		t.Run(name, func(t *testing.T) {
			dir := t.TempDir()
			path := filepath.Join(dir, "modemst2.bin")
			usb := &scriptedUSB{packets: packets}
			device := &Device{inEP: usb, outEP: usb}
			if err := device.OEMDump("modemst2", path); err == nil {
				t.Fatal("incomplete backup accepted")
			}
			entries, _ := os.ReadDir(dir)
			if len(entries) != 0 {
				t.Fatalf("partial output survived: %v", entries)
			}
		})
	}
}

func TestOEMDumpDoesNotOverwriteExistingBackupOrConcurrentLock(t *testing.T) {
	for _, suffix := range []string{"", ".lock"} {
		t.Run(suffix, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "modemst1.bin")
			if err := os.WriteFile(path+suffix, []byte("KEEP"), 0600); err != nil {
				t.Fatal(err)
			}
			usb := &scriptedUSB{}
			device := &Device{inEP: usb, outEP: usb}
			if err := device.OEMDump("modemst1", path); err == nil {
				t.Fatal("existing backup/lock accepted")
			}
			if len(usb.writes) != 0 {
				t.Fatal("USB used before destination checks")
			}
			data, _ := os.ReadFile(path + suffix)
			if string(data) != "KEEP" {
				t.Fatal("existing file changed")
			}
		})
	}
}

// This emulates the bootloader, interpreting each sparse transfer from block
// zero and preserving DONT_CARE blocks. It does not append raw flash payloads.
type flashUSB struct {
	packets   []responsePacket
	staged    []byte
	expected  int
	image     []byte
	transfers [][]byte
}

func (s *flashUSB) Read(p []byte) (int, error) {
	reader := scriptedUSB{packets: s.packets}
	n, err := reader.Read(p)
	s.packets = reader.packets
	return n, err
}
func (s *flashUSB) Write(data []byte) (int, error) {
	if s.expected > 0 {
		s.staged = append(s.staged, data...)
		if len(s.staged) > s.expected {
			return 0, errors.New("download overflow")
		}
		if len(s.staged) == s.expected {
			s.expected = 0
			s.packets = append(s.packets, packet("OKAY"))
		}
		return len(data), nil
	}
	command := string(data)
	switch {
	case strings.HasPrefix(command, "download:"):
		var count int
		if _, err := fmt.Sscanf(command, "download:%08x", &count); err != nil {
			return 0, err
		}
		s.expected = count
		s.staged = nil
		s.packets = append(s.packets, packet(fmt.Sprintf("DATA%08x", count)))
	case command == "flash:rootfs":
		if err := applyTestSparse(s.image, s.staged); err != nil {
			return 0, err
		}
		s.transfers = append(s.transfers, append([]byte(nil), s.staged...))
		s.packets = append(s.packets, packet("OKAY"))
	default:
		return 0, fmt.Errorf("unexpected command %q", command)
	}
	return len(data), nil
}
func applyTestSparse(image, data []byte) error {
	if len(data) < 28 || binary.LittleEndian.Uint32(data[:4]) != 0xed26ff3a {
		return errors.New("not a sparse image")
	}
	fileHeader := int(binary.LittleEndian.Uint16(data[8:10]))
	chunkHeader := int(binary.LittleEndian.Uint16(data[10:12]))
	blockSize := int(binary.LittleEndian.Uint32(data[12:16]))
	if blockSize*int(binary.LittleEndian.Uint32(data[16:20])) != len(image) {
		return errors.New("logical size mismatch")
	}
	pos, block := fileHeader, 0
	chunks := int(binary.LittleEndian.Uint32(data[20:24]))
	for i := 0; i < chunks; i++ {
		if pos+chunkHeader > len(data) {
			return io.ErrUnexpectedEOF
		}
		kind := binary.LittleEndian.Uint16(data[pos : pos+2])
		count := int(binary.LittleEndian.Uint32(data[pos+4 : pos+8]))
		total := int(binary.LittleEndian.Uint32(data[pos+8 : pos+12]))
		end := (block + count) * blockSize
		if end > len(image) || pos+total > len(data) || total < chunkHeader {
			return errors.New("chunk extent")
		}
		payload := data[pos+chunkHeader : pos+total]
		switch kind {
		case 0xcac1:
			if len(payload) != count*blockSize {
				return errors.New("RAW size")
			}
			copy(image[block*blockSize:end], payload)
		case 0xcac2:
			if len(payload) != 4 {
				return errors.New("FILL size")
			}
			for j := block * blockSize; j < end; j++ {
				image[j] = payload[(j-block*blockSize)%4]
			}
		case 0xcac3:
			if len(payload) != 0 {
				return errors.New("SKIP size")
			}
		default:
			return fmt.Errorf("unexpected emitted chunk %x", kind)
		}
		block += count
		pos += total
	}
	if block*blockSize != len(image) || pos != len(data) {
		return errors.New("sparse totals mismatch")
	}
	return nil
}
func testImage(t *testing.T, data []byte) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "system.img")
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestRawImageFragmentsPreserveOffsetsAndDownloadLimit(t *testing.T) {
	data := make([]byte, 5*4096)
	for block := 0; block < 5; block++ {
		for i := block * 4096; i < (block+1)*4096; i++ {
			data[i] = byte(block + 1)
		}
	}
	usb := &flashUSB{image: bytes.Repeat([]byte{0x77}, len(data))}
	device := &Device{inEP: usb, outEP: usb}
	limit := 28 + 36 + 2*4096
	last := 0
	err := device.FlashFileSparse("rootfs", testImage(t, data), limit, func(sent, total int) {
		if sent < last || sent > total || total != len(data) {
			t.Fatalf("progress %d/%d after %d", sent, total, last)
		}
		last = sent
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(usb.transfers) != 3 {
		t.Fatalf("wanted three valid sparse fragments, got %d", len(usb.transfers))
	}
	for _, fragment := range usb.transfers {
		if len(fragment) > limit {
			t.Fatal("oversized transfer")
		}
	}
	if !bytes.Equal(usb.image, data) {
		t.Fatal("later fragments overwrote the start of rootfs")
	}
	if last != len(data) {
		t.Fatal("incomplete progress")
	}
}

func makeSparseFixture() ([]byte, []byte) {
	blockSize := 4096
	raw1, raw2 := bytes.Repeat([]byte{0x13}, 2*blockSize), bytes.Repeat([]byte{0x27}, blockSize)
	data := make([]byte, 28)
	binary.LittleEndian.PutUint32(data[0:4], 0xed26ff3a)
	binary.LittleEndian.PutUint16(data[4:6], 1)
	binary.LittleEndian.PutUint16(data[8:10], 28)
	binary.LittleEndian.PutUint16(data[10:12], 12)
	binary.LittleEndian.PutUint32(data[12:16], uint32(blockSize))
	binary.LittleEndian.PutUint32(data[16:20], 5)
	binary.LittleEndian.PutUint32(data[20:24], 5)
	add := func(kind uint16, blocks uint32, payload []byte) {
		h := make([]byte, 12)
		binary.LittleEndian.PutUint16(h[:2], kind)
		binary.LittleEndian.PutUint32(h[4:8], blocks)
		binary.LittleEndian.PutUint32(h[8:12], uint32(12+len(payload)))
		data = append(data, h...)
		data = append(data, payload...)
	}
	add(0xcac1, 2, raw1)
	add(0xcac2, 1, []byte{1, 2, 3, 4})
	add(0xcac3, 1, nil)
	add(0xcac1, 1, raw2)
	logical := append([]byte(nil), raw1...)
	logical = append(logical, bytes.Repeat([]byte{1, 2, 3, 4}, blockSize/4)...)
	logical = append(logical, make([]byte, blockSize)...)
	logical = append(logical, raw2...)
	checksum := crc32.ChecksumIEEE(logical)
	word := make([]byte, 4)
	binary.LittleEndian.PutUint32(word, checksum)
	add(0xcac4, 0, word)
	binary.LittleEndian.PutUint32(data[24:28], checksum)
	expected := append([]byte(nil), logical...)
	copy(expected[3*blockSize:4*blockSize], bytes.Repeat([]byte{0x77}, blockSize))
	return data, expected
}

func TestExistingSparseRawFillHolesAndCRCResplitCorrectly(t *testing.T) {
	data, want := makeSparseFixture()
	path := testImage(t, data)
	usb := &flashUSB{image: bytes.Repeat([]byte{0x77}, len(want))}
	device := &Device{inEP: usb, outEP: usb}
	if err := device.FlashFileSparse("rootfs", path, 28+36+4096, nil); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(usb.image, want) {
		t.Fatal("sparse data or DONT_CARE offsets changed")
	}
	if len(usb.transfers) != 4 {
		t.Fatalf("wrong fragment count %d", len(usb.transfers))
	}
	size, err := SparseImageSize(path)
	if err != nil || size != int64(len(want)) {
		t.Fatalf("blk_sz decoded from wrong header offset: %d %v", size, err)
	}
}

func TestInvalidOrTruncatedImagesAreRejectedBeforeUSB(t *testing.T) {
	valid, _ := makeSparseFixture()
	badCRC := append([]byte(nil), valid...)
	badCRC[len(badCRC)-1] ^= 1
	badBlock := append([]byte(nil), valid...)
	binary.LittleEndian.PutUint32(badBlock[12:16], 0)
	badExtent := append([]byte(nil), valid...)
	binary.LittleEndian.PutUint32(badExtent[28+8:28+12], 0)
	cases := map[string][]byte{"empty": nil, "unaligned raw": []byte("not aligned"), "truncated": valid[:len(valid)-1],
		"CRC mismatch": badCRC, "zero block": badBlock, "bad chunk size": badExtent, "trailing bytes": append(append([]byte(nil), valid...), 0)}
	for name, data := range cases {
		t.Run(name, func(t *testing.T) {
			usb := &scriptedUSB{}
			device := &Device{inEP: usb, outEP: usb}
			if err := device.FlashFileSparse("rootfs", testImage(t, data), 200*1024*1024, nil); err == nil {
				t.Fatal("invalid image accepted")
			}
			if len(usb.writes) != 0 {
				t.Fatal("device touched before full image validation")
			}
		})
	}
	for _, limit := range []int{-1, 0, 64, 4096} {
		usb := &scriptedUSB{}
		device := &Device{inEP: usb, outEP: usb}
		if err := device.FlashFileSparse("rootfs", testImage(t, make([]byte, 4096)), limit, nil); err == nil {
			t.Fatalf("bad limit accepted: %d", limit)
		}
		if len(usb.writes) != 0 {
			t.Fatal("device touched with invalid fragment limit")
		}
	}
}

func TestFlashRejectsWrongDownloadSizeAndShortWrites(t *testing.T) {
	usb := &scriptedUSB{packets: []responsePacket{packet("DATA00000001")}}
	device := &Device{inEP: usb, outEP: usb}
	if err := device.Flash("rootfs", []byte("abcd"), nil); err == nil {
		t.Fatal("wrong DATA size accepted")
	}
	if len(usb.writes) != 1 {
		t.Fatal("data/flash sent after size mismatch")
	}
	usb = &scriptedUSB{short: true}
	device = &Device{inEP: usb, outEP: usb}
	if err := device.Flash("rootfs", []byte("abcd"), nil); !errors.Is(err, io.ErrShortWrite) {
		t.Fatalf("short command write: %v", err)
	}
}
