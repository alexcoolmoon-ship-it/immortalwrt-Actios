package fastboot

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// Verify the emitted wire files with an independent Android implementation,
// not only the bootloader model in fastboot_test.go.
func TestFragmentsRoundTripThroughAndroidLibsparse(t *testing.T) {
	for _, tool := range []string{"simg2img", "img2simg"} {
		if _, err := exec.LookPath(tool); err != nil {
			t.Skip("install android-sdk-libsparse-utils for Android interoperability checks")
		}
	}
	raw := make([]byte, 32*4096)
	for i := range raw {
		if i >= 8*4096 && i < 16*4096 {
			continue
		}
		raw[i] = byte((i*31 + i/11) % 251)
	}
	dir := t.TempDir()
	rawPath := filepath.Join(dir, "original.raw")
	if err := os.WriteFile(rawPath, raw, 0600); err != nil {
		t.Fatal(err)
	}
	sparsePath := filepath.Join(dir, "android.sparse")
	if output, err := exec.Command("img2simg", rawPath, sparsePath).CombinedOutput(); err != nil {
		t.Fatalf("img2simg: %v %s", err, output)
	}
	for _, path := range []string{rawPath, sparsePath} {
		t.Run(filepath.Base(path), func(t *testing.T) {
			file, err := os.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			defer file.Close()
			plan, err := readSparsePlan(file, 28+36+3*4096)
			if err != nil {
				t.Fatal(err)
			}
			var fragments []string
			err = plan.flash(file, 28+36+3*4096, func(data []byte, start, length int64) error {
				name := filepath.Join(dir, fmt.Sprintf("%s-%02d.sparse", filepath.Base(path), len(fragments)))
				if err := os.WriteFile(name, data, 0600); err != nil {
					return err
				}
				fragments = append(fragments, name)
				return nil
			})
			if err != nil {
				t.Fatal(err)
			}
			if len(fragments) < 2 {
				t.Fatal("test did not exercise multiple flash fragments")
			}
			outputPath := filepath.Join(dir, filepath.Base(path)+".restored")
			args := append(append([]string(nil), fragments...), outputPath)
			if output, err := exec.Command("simg2img", args...).CombinedOutput(); err != nil {
				t.Fatalf("simg2img: %v %s", err, output)
			}
			restored, err := os.ReadFile(outputPath)
			if err != nil || !bytes.Equal(restored, raw) {
				t.Fatalf("Android round trip changed image: %v", err)
			}
		})
	}
}
