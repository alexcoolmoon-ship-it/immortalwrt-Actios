package fastboot

// Android sparse format:
// https://android.googlesource.com/platform/system/core/+/android-15.0.0_r1/libsparse/sparse_format.h
// Each transfer describes the full logical image. DONT_CARE chunks before and
// after its payload retain offsets across separate flash commands.
import (
	"encoding/binary"
	"fmt"
	"hash/crc32"
	"io"
	"os"
)

const (
	sparseMagic              = 0xed26ff3a
	sparseHeader             = 28
	sparseChunkHeader        = 12
	sparseRAW         uint16 = 0xcac1
	sparseFILL        uint16 = 0xcac2
	sparseSKIP        uint16 = 0xcac3
	sparseCRC         uint16 = 0xcac4
)

type sparseSegment struct {
	kind          uint16
	start, blocks uint32
	offset        int64
	value         uint32
}

type sparsePlan struct {
	blockSize, blocks uint32
	segments          []sparseSegment
	checksum          uint32
}

func (p *sparsePlan) byteSize() int64 {
	return int64(p.blockSize) * int64(p.blocks)
}

// ValidateSparseFile can be called before any device writes.
func ValidateSparseFile(path string, fragmentLimit int) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = readSparsePlan(f, fragmentLimit)
	return err
}

func readSparsePlan(f *os.File, fragmentLimit int) (*sparsePlan, error) {
	if fragmentLimit <= sparseHeader+3*sparseChunkHeader ||
		uint64(fragmentLimit) > uint64(^uint32(0)) {
		return nil, fmt.Errorf("invalid sparse fragment limit: %d", fragmentLimit)
	}
	info, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Size() == 0 {
		return nil, fmt.Errorf("image must be a nonempty regular file")
	}
	var header [sparseHeader]byte
	if _, err := f.ReadAt(header[:4], 0); err != nil {
		return nil, err
	}
	p := &sparsePlan{}
	if binary.LittleEndian.Uint32(header[:4]) != sparseMagic {
		// Rootfs raw images have whole 4 KiB filesystem blocks. Do not
		// silently pad unaligned input or erase data past its actual end.
		if info.Size()%4096 != 0 || uint64(info.Size()/4096) > uint64(^uint32(0)) {
			return nil, fmt.Errorf("raw image must contain complete 4096-byte blocks")
		}
		p.blockSize, p.blocks = 4096, uint32(info.Size()/4096)
		p.segments = []sparseSegment{{kind: sparseRAW, blocks: p.blocks}}
	} else {
		if _, err := f.ReadAt(header[:], 0); err != nil {
			return nil, err
		}
		fileHeader := int64(binary.LittleEndian.Uint16(header[8:10]))
		chunkHeader := int64(binary.LittleEndian.Uint16(header[10:12]))
		p.blockSize = binary.LittleEndian.Uint32(header[12:16])
		p.blocks = binary.LittleEndian.Uint32(header[16:20])
		chunks := binary.LittleEndian.Uint32(header[20:24])
		p.checksum = binary.LittleEndian.Uint32(header[24:28])
		if binary.LittleEndian.Uint16(header[4:6]) != 1 ||
			fileHeader < sparseHeader || chunkHeader < sparseChunkHeader ||
			p.blockSize == 0 || p.blockSize%4 != 0 || p.blocks == 0 ||
			fileHeader > info.Size() || chunks == 0 ||
			int64(chunks) > (info.Size()-fileHeader)/chunkHeader {
			return nil, fmt.Errorf("invalid Android sparse header")
		}
		offset, position := fileHeader, uint64(0)
		for i := uint32(0); i < chunks; i++ {
			var h [sparseChunkHeader]byte
			if _, err := f.ReadAt(h[:], offset); err != nil {
				return nil, err
			}
			kind := binary.LittleEndian.Uint16(h[:2])
			blocks := binary.LittleEndian.Uint32(h[4:8])
			total := int64(binary.LittleEndian.Uint32(h[8:12]))
			if total < chunkHeader || total > info.Size()-offset ||
				position+uint64(blocks) > uint64(p.blocks) {
				return nil, fmt.Errorf("invalid sparse chunk %d extent", i)
			}
			segment := sparseSegment{kind: kind, start: uint32(position), blocks: blocks, offset: offset + chunkHeader}
			payload := total - chunkHeader
			switch kind {
			case sparseRAW:
				if blocks == 0 || uint64(payload) != uint64(blocks)*uint64(p.blockSize) {
					return nil, fmt.Errorf("invalid RAW chunk %d length", i)
				}
			case sparseFILL, sparseCRC:
				if payload != 4 || (kind == sparseCRC && blocks != 0) ||
					(kind == sparseFILL && blocks == 0) {
					return nil, fmt.Errorf("invalid FILL/CRC chunk %d", i)
				}
				var word [4]byte
				if _, err := f.ReadAt(word[:], segment.offset); err != nil {
					return nil, err
				}
				segment.value = binary.LittleEndian.Uint32(word[:])
			case sparseSKIP:
				if blocks == 0 || payload != 0 {
					return nil, fmt.Errorf("invalid DONT_CARE chunk %d", i)
				}
			default:
				return nil, fmt.Errorf("unknown sparse chunk type %04x", kind)
			}
			p.segments = append(p.segments, segment)
			position += uint64(blocks)
			offset += total
		}
		if position != uint64(p.blocks) || offset != info.Size() {
			return nil, fmt.Errorf("sparse image length/block count mismatch")
		}
	}
	logical := uint64(p.blockSize) * uint64(p.blocks)
	if logical > uint64(^uint(0)>>1) ||
		int64(fragmentLimit)-sparseHeader-3*sparseChunkHeader < int64(p.blockSize) {
		return nil, fmt.Errorf("image/block size exceeds host or fragment limit")
	}
	needsChecksum := p.checksum != 0
	for _, s := range p.segments {
		needsChecksum = needsChecksum || s.kind == sparseCRC
	}
	if needsChecksum {
		if err := p.verifyChecksum(f); err != nil {
			return nil, err
		}
	}
	return p, nil
}

func (p *sparsePlan) verifyChecksum(f *os.File) error {
	hash := crc32.NewIEEE()
	for _, s := range p.segments {
		length := int64(s.blocks) * int64(p.blockSize)
		switch s.kind {
		case sparseRAW:
			if _, err := io.CopyN(hash, io.NewSectionReader(f, s.offset, length), length); err != nil {
				return err
			}
		case sparseFILL:
			if err := writeRepeated(hash, s.value, length); err != nil {
				return err
			}
		case sparseSKIP:
			if err := writeRepeated(hash, 0, length); err != nil {
				return err
			}
		case sparseCRC:
			if s.value != hash.Sum32() {
				return fmt.Errorf("sparse CRC chunk mismatch")
			}
		}
	}
	if p.checksum != 0 && p.checksum != hash.Sum32() {
		return fmt.Errorf("sparse image CRC mismatch")
	}
	return nil
}

func writeRepeated(w io.Writer, value uint32, length int64) error {
	buffer := make([]byte, 64*1024)
	for i := 0; i < len(buffer); i += 4 {
		binary.LittleEndian.PutUint32(buffer[i:i+4], value)
	}
	for length > 0 {
		n := int64(len(buffer))
		if n > length {
			n = length
		}
		written, err := w.Write(buffer[:int(n)])
		if err != nil {
			return err
		}
		if written != int(n) {
			return io.ErrShortWrite
		}
		length -= n
	}
	return nil
}

func (p *sparsePlan) fragment(f *os.File, s sparseSegment, start, blocks uint32) ([]byte, error) {
	payload := 0
	if s.kind == sparseRAW {
		payload = int(blocks) * int(p.blockSize)
	}
	if s.kind == sparseFILL {
		payload = 4
	}
	chunks := uint32(1)
	if start > 0 {
		chunks++
	}
	end := start + blocks
	if end < p.blocks {
		chunks++
	}
	buf := make([]byte, sparseHeader+int(chunks)*sparseChunkHeader+payload)
	binary.LittleEndian.PutUint32(buf[0:4], sparseMagic)
	binary.LittleEndian.PutUint16(buf[4:6], 1)
	binary.LittleEndian.PutUint16(buf[8:10], sparseHeader)
	binary.LittleEndian.PutUint16(buf[10:12], sparseChunkHeader)
	binary.LittleEndian.PutUint32(buf[12:16], p.blockSize)
	binary.LittleEndian.PutUint32(buf[16:20], p.blocks)
	binary.LittleEndian.PutUint32(buf[20:24], chunks)
	pos := sparseHeader
	add := func(kind uint16, count uint32, size int) {
		binary.LittleEndian.PutUint16(buf[pos:pos+2], kind)
		binary.LittleEndian.PutUint32(buf[pos+4:pos+8], count)
		binary.LittleEndian.PutUint32(buf[pos+8:pos+12], uint32(sparseChunkHeader+size))
		pos += sparseChunkHeader
	}
	if start > 0 {
		add(sparseSKIP, start, 0)
	}
	add(s.kind, blocks, payload)
	switch s.kind {
	case sparseRAW:
		offset := s.offset + int64(start-s.start)*int64(p.blockSize)
		if _, err := f.ReadAt(buf[pos:pos+payload], offset); err != nil {
			return nil, err
		}
	case sparseFILL:
		binary.LittleEndian.PutUint32(buf[pos:pos+4], s.value)
	}
	pos += payload
	if end < p.blocks {
		add(sparseSKIP, p.blocks-end, 0)
	}
	return buf, nil
}

func (p *sparsePlan) flash(f *os.File, limit int, send func([]byte, int64, int64) error) error {
	maxBlocks := uint32((int64(limit) - sparseHeader - 3*sparseChunkHeader) / int64(p.blockSize))
	wrote := false
	for _, s := range p.segments {
		if s.kind != sparseRAW && s.kind != sparseFILL {
			continue
		}
		for done := uint32(0); done < s.blocks; {
			count := s.blocks - done
			if s.kind == sparseRAW && count > maxBlocks {
				count = maxBlocks
			}
			start := s.start + done
			buf, err := p.fragment(f, s, start, count)
			if err != nil {
				return err
			}
			if len(buf) > limit {
				return fmt.Errorf("sparse fragment exceeds download limit")
			}
			if err := send(buf, int64(start)*int64(p.blockSize), int64(count)*int64(p.blockSize)); err != nil {
				return err
			}
			done += count
			wrote = true
		}
	}
	if !wrote {
		buf, err := p.fragment(f, sparseSegment{kind: sparseSKIP}, 0, p.blocks)
		if err != nil {
			return err
		}
		return send(buf, 0, p.byteSize())
	}
	return nil
}
