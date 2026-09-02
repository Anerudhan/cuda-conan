import os
import shutil
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

from conan import ConanFile
from conan.errors import ConanException, ConanInvalidConfiguration
from conan.tools.files import copy, download, replace_in_file


required_conan_version = ">=2.1.0"


class CudaToolkitConan(ConanFile):
    name = "cuda-toolkit"
    version = "13.4.0-preview"
    package_type = "shared-library"
    license = "CUDA Toolkit End-User License Agreement"
    url = "https://github.com/conan-io/cuda-conan"
    homepage = "https://developer.nvidia.com/cuda-toolkit"
    description = "A development environment for creating high performance GPU-accelerated applications"
    settings = "os", "arch", "compiler", "build_type"

    @property
    def _cuda_root(self):
        # Where the extracted debs put the toolkit, mirroring an on-disk /usr/local install
        return os.path.join(self.build_folder, "deb", "usr", "local", "cuda-13.4")

    @property
    def _target_arch(self):
        return "sbsa" if self.settings.arch == "armv8" else str(self.settings.arch)

    def validate(self):
        if self.settings.os not in ["Windows", "Linux"]:
            raise ConanInvalidConfiguration("Only Windows and Linux supported for the 13.4.0 developer preview")

    def requirements(self):
        if self.settings.os == "Windows" and self.settings_build.os == "Windows":
            self.tool_requires("7zip/[*]")

    def build(self):
        if self.settings.os == "Windows":
            self._build_windows()
        else:
            self._build_linux()

    def _build_windows(self):
        host_os = str(self.settings.os)
        arch = str(self.settings.arch)

        installer_url = self.conan_data['sources'][self.version][host_os][arch]['url']
        installer_md5 = self.conan_data['sources'][self.version][host_os][arch]['md5']
        installer_sha256 = self.conan_data['sources'][self.version][host_os][arch]['sha256']

        self.output.info(f'Cuda installer URL: {installer_url}')
        installer_filename = "installer"
        download(self, installer_url, installer_filename, md5=installer_md5, sha256=installer_sha256)

        self.run(f"7z x {installer_filename} -o{self.build_folder}")

        # When cross-building, get nvcc to locate libraries in host package
        replace_in_file(
            self,
            "cuda_nvcc/nvcc/bin/nvcc.profile",
            'LIBRARIES        =+ $(_SPACE_) "/LIBPATH:$(TOP)/lib/$(_WIN_PLATFORM_)"',
            'CONAN_CUDA_LIB_DIR ?= $(TOP)/lib/$(_WIN_PLATFORM_)\n'
            'LIBRARIES        =+ $(_SPACE_) "/LIBPATH:$(CONAN_CUDA_LIB_DIR)"',
        )

    def _build_linux(self):
        # The developer preview ships no tarball installer and no redistrib json, so the
        # toolkit is assembled from the prerelease distro packages instead. Their payload
        # already uses the targets/<arch>-linux layout, so they only need unpacking.
        packages = self.conan_data['sources'][self.version]['Linux'][str(self.settings.arch)]

        destination = os.path.join(self.build_folder, "deb")
        for name, data in packages.items():
            self.output.info(f"About to download {name}")
            deb = f"{name}.deb"
            download(self, data['url'], deb, sha256=data['sha256'])
            self._extract_deb(deb, destination)

        # When crossbuilding, point nvcc to the target directory in the "host" package (for the target
        # architecture), rather than have it look relative to itself (which is the current architecture
        # of the build machine)
        nvcc_profile = os.path.join(self._cuda_root, "bin", "nvcc.profile")
        replace_in_file(self, nvcc_profile, '$(TOP)/$(_TARGET_DIR_)/', '$(CONAN_CUDA_TARGET_DIR)/')
        replace_in_file(self, nvcc_profile, "lib$(_TARGET_SIZE_)", "lib")

    def _extract_deb(self, deb, destination):
        # A deb is an ar archive whose data member holds the payload. Components overlap on
        # shared entries such as the top level include and lib64 symlinks, so anything already
        # unpacked by an earlier package is left alone.
        with open(deb, "rb") as archive:
            if archive.read(8) != b"!<arch>\n":
                raise ConanException(f"{deb} is not a Debian package")

            while True:
                header = archive.read(60)
                if len(header) < 60:
                    raise ConanException(f"{deb} contains no data member")

                name = header[:16].decode().strip().rstrip("/")
                size = int(header[48:58].decode().strip())
                if not name.startswith("data.tar"):
                    archive.seek(size + size % 2, os.SEEK_CUR)
                    continue

                payload = f"{deb}.data"
                with open(payload, "wb") as data:
                    shutil.copyfileobj(archive, data, size)
                break

        with tarfile.open(payload) as tar:
            for member in tar:
                if os.path.lexists(os.path.join(destination, member.name)):
                    continue
                tar.extract(member, destination, filter="data")

        os.remove(payload)
        os.remove(deb)

    def package(self):
        if self.settings.os == "Linux":
            self._package_linux()
        else:
            self._package_windows()

    def _package_linux(self):
        copy(self, "copyright", src=os.path.join(self.build_folder, "deb", "usr", "share", "doc"),
                                dst=os.path.join(self.package_folder, "licenses"))
        copy(self, "*", src=self._cuda_root, dst=self.package_folder)

        # The debs ship include and lib64 as symlinks into targets/<arch>-linux already, but
        # recreate any that did not survive the copy so consumers see the usual layout
        for link_name, target in [("include", "include"), ("lib64", "lib"), ("res", "res")]:
            link = Path(self.package_folder) / link_name
            destination = Path(self.package_folder) / "targets" / f"{self._target_arch}-linux" / target
            if not link.exists() and destination.is_dir():
                link.symlink_to(Path("targets") / f"{self._target_arch}-linux" / target)

    def _package_windows(self):
        components = [
            "cccl/cccl/cccl.nvi",
            "cuda_crt/crt/crt.nvi",
            "cuda_cudart/cudart/cudart.nvi",
            "cuda_cuobjdump/cuobjdump/cuobjdump.nvi",
            "cuda_cupti/cupti/cupti.nvi",
            "cuda_cuxxfilt/cuxxfilt/cuxxfilt.nvi",
            "cuda_nvcc/nvcc/nvcc.nvi",
            "cuda_nvdisasm/nvdisasm/nvdisasm.nvi",
            "cuda_nvml_dev/nvml_dev/nvml_dev.nvi",
            "cuda_nvprune/nvprune/nvprune.nvi",
            "cuda_nvrtc/nvrtc_dev/nvrtc_dev.nvi",
            "cuda_nvrtc/nvrtc/nvrtc.nvi",
            "cuda_nvtx/nvtx/nvtx.nvi",
            "cuda_opencl/opencl/opencl.nvi",
            "cuda_profiler_api/cuda_profiler_api/cuda_profiler_api.nvi",
            "cuda_sanitizer_api/sanitizer/sanitizer.nvi",
            "cuda_tileiras/tileiras/tileiras.nvi",
            "CUDAToolkit/CUDAToolkit.nvi",
            "libcublas/cublas_dev/cublas_dev.nvi",
            "libcublas/cublas/cublas.nvi",
            "libcufft/cufft_dev/cufft_dev.nvi",
            "libcufft/cufft/cufft.nvi",
            "libcurand/curand_dev/curand_dev.nvi",
            "libcurand/curand/curand.nvi",
            "libcusolver/cusolver_dev/cusolver_dev.nvi",
            "libcusolver/cusolver/cusolver.nvi",
            "libcusparse/cusparse_dev/cusparse_dev.nvi",
            "libcusparse/cusparse/cusparse.nvi",
            "libnpp/npp_dev/npp_dev.nvi",
            "libnpp/npp/npp.nvi",
            "libnvjitlink/nvjitlink/nvjitlink.nvi",
            "libnvjpeg/nvjpeg_dev/nvjpeg_dev.nvi",
            "libnvjpeg/nvjpeg/nvjpeg.nvi",
            "libnvfatbin/nvfatbin/nvfatbin.nvi",
            "libnvptxcompiler/libnvptxcompiler/libnvptxcompiler.nvi",
            "libnvvm/nvvm/nvvm.nvi",
            "visual_studio_integration/CUDAVisualStudioIntegration/CUDAVisualStudioIntegration.nvi"
        ]

        if self.settings.arch == "armv8":
            components.append("libcudla/cudla/cudla.nvi")

        for nvi_file in components:
            source_folder = os.path.dirname(nvi_file)
            self.output.info(f"Component folder: {source_folder}")
            full_nvi_file = f"{self.build_folder}/{nvi_file}"

            tree = ET.parse(full_nvi_file).getroot()

            for x in tree.findall("phases/standard[@phase='copyFiles']/copyFile"):
                if 'source' in x.attrib:
                    continue

                target = x.attrib['target']
                rel_dir = os.path.dirname(target)
                filename = os.path.basename(target)

                src = os.path.join(self.build_folder, source_folder, rel_dir)
                dest = os.path.join(self.package_folder, rel_dir)
                copy(self, filename, src, dest, keep_path=False)

    def package_id(self):
        self.info.settings.rm_safe("compiler")
        self.info.settings.rm_safe("build_type")

    def package_info(self):
        self.cpp_info.set_property("cmake_file_name", "CUDAToolkit") # what consumers will expect
        self.cpp_info.set_property("cmake_find_mode", "none") # but don't generate it
        self.cpp_info.set_property("cmake_target_name", "CUDA::toolkit")

        nvcc = "nvcc.exe" if self.settings.os == "Windows" else "nvcc"
        self.buildenv_info.define_path("CUDACXX", os.path.join(self.package_folder, "bin", nvcc))

        if self.settings.os == "Linux":
            if not self.settings_target:
                self.buildenv_info.define_path("CUDAToolkit_ROOT", self.package_folder)
                self.buildenv_info.define_path("CONAN_CUDA_TARGET_DIR",
                                               os.path.join(self.package_folder, "targets", f"{self._target_arch}-linux"))

            self.cpp_info.libdirs = ["lib64"]
            self.cpp_info.bindirs = ["bin"]

            if self.settings_target and self.settings_target.get_safe("build_type"):
                self.conf_info.update("tools.cmake.cmaketoolchain:extra_variables",
                                        {"CMAKE_TRY_COMPILE_CONFIGURATION": str(self.settings_target.build_type)})
            return

        arch = "arm64" if self.settings.arch == "armv8" else "x64"
        if not self.settings_target:
            self.buildenv_info.define_path("CUDAToolkit_ROOT", self.package_folder)
            self.buildenv_info.define_path("CONAN_CUDA_LIB_DIR", os.path.join(self.package_folder, "lib", arch).replace("\\", "/"))
       
        self.cpp_info.libdirs = [f"lib/{arch}", f"nvvm/lib/{arch}"]
        self.cpp_info.bindirs = ["bin", f"bin/{arch}", "nvvm/lib"]

        # The following only effect CMake generator is Visual Studio
        package_folder = f"{self.package_folder}".replace('\\', '/')
        self.conf_info.define("tools.cmake.cmaketoolchain:toolset_cuda", package_folder)

        if self.settings_target and self.settings_target.get_safe("build_type"):
            arch = "arm64" if self.settings_target.arch == "armv8" else "x64"
            self.conf_info.update("tools.cmake.cmaketoolchain:extra_variables",
                                    {"CMAKE_TRY_COMPILE_CONFIGURATION": str(self.settings_target.build_type),
                                     "CMAKE_TRY_COMPILE_PLATFORM_VARIABLES": "CMAKE_VS_GLOBALS",
                                     "CMAKE_VS_GLOBALS": f"CudaToolkitLibDir=$ENV{{CUDAToolkit_ROOT}}/lib/{arch}"})
        
