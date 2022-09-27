import os
import dataclasses
import yaml
import subprocess
import multiprocessing

ROOT = os.path.dirname(__file__)
ISO_PATH = os.path.join(ROOT, 'iso')
DISK_PATH = os.path.join(ROOT, 'disk')

SCRIPT_CONFIG = yaml.load(
	open(os.path.join(ROOT, 'config.yml')).read(),
	yaml.SafeLoader,
)

@dataclasses.dataclass
class Config:
	memory: str
	cpus: int
	disk_size: str
	arch: str

	image: str
	image_path: str
	disk_path: str
	ssh_port: int


def sh(cmd, **kwargs):
	cmd = [str(c) for c in cmd]
	print('> ', ' '.join(cmd))
	return subprocess.run(cmd, check=True, text=True, **kwargs)

def img_config(image):
	return SCRIPT_CONFIG['images'][image]


def setup(vm_cnf: Config):
	if not os.path.isfile(vm_cnf.image_path):
		img_cnf = img_config(vm_cnf.image)

		os.makedirs(os.path.dirname(vm_cnf.image_path), exist_ok=True)
		print('Downloading image...')
		sh(['curl', '-L', '--progress-bar', img_cnf['url'], '-o', vm_cnf.image_path])


	if not os.path.isfile(vm_cnf.disk_path):
		os.makedirs(os.path.dirname(vm_cnf.disk_path), exist_ok=True)
		sh(['qemu-img', 'create', '-f', 'qcow2', vm_cnf.disk_path, vm_cnf.disk_size])


def qemu_cnf(vm_cnf):
	if vm_cnf.arch == 'arm64':
		QEMU_DIR='/opt/homebrew/share/qemu'
		drive_path = os.path.join(QEMU_DIR, 'edk2-aarch64-code.fd')
		return [
			'qemu-system-aarch64',
			'-display', 'none',
			'-serial', 'mon:stdio',
			'-m', vm_cnf.memory,
			'-smp', vm_cnf.cpus,
			'-M', 'virt,highmem=on',
			'-device', 'virtio-net-pci,netdev=n1',
			'-netdev', f'user,id=n1,hostfwd=tcp::{vm_cnf.ssh_port}-:22',
			'-accel', 'hvf',
			'-accel', 'tcg',
			'-cpu', 'host',
			'-drive', f'file={drive_path},if=pflash,format=raw,readonly=on',
			'-drive', f'file={vm_cnf.disk_path}',
		]
	raise NotImplementedError()

def start(vm_cnf: Config):
	sh(qemu_cnf(vm_cnf))


def bootstrap(vm_cnf: Config):
	proc = run_cloudinit(1234)
	gen_cloudinit()
	sh(qemu_cnf(vm_cnf) + [
		'-cdrom', vm_cnf.image_path,
	])
	# proc.kill()
	proc.join()


def _run_cloudinit(port: int):
	import http.server
	server_address = ('', port)
	server_class = http.server.ThreadingHTTPServer
	httpd = server_class(server_address, handler_class)
	httpd.serve_forever()

def run_cloudinit(PORT):
	return multiprocessing.Process(target=_run_cloudinit, args=(PORT,))


def gen_cloudinit(**args):
	# https://ubuntu.com/server/docs/install/autoinstall-quickstart
	tmp_dir = './tmp'

	hostname = args.get('hostname', 'bubuntu')
	username = args.get('username', 'bubuntu')
	pw = '$6$exDY1mhS4KUYCE/2$zmn9ToZwTKLhCw.b4/b.ZRTIZM30JZ4QrOQ2aOXJ8yk96xpcCof0kxKwuX1kqLG/ygbJ1f8wxED22bTL4F46P0'

	os.makedirs(tmp_dir, exist_ok=True)
	userdata_file = os.path.join(tmp_dir, 'user-data')
	metadata_file = os.path.join(tmp_dir, 'meta-data')

	with open(metadata_file, 'w'):
		pass

	with open(userdata_file, 'w') as fd:
		 fd.write('\n'.join([
			 f'autoinstall:',
			 f'  version: 1',
			 f'  identity:',
			 f'    hostname: {hostname}',
			 f'    password: {pw}',
			 f'    username: {username}',
			 ]))

	with open(os.path.join(tmp_dir, 'gen_iso.sh'), 'w') as fd:
		fd.write('\n'.join([
			'#!/usr/bin/env sh',
			'apk add --no-cache cloud-utils-localds',
			'cloud-localds ~/seed.iso user-data meta-data',
		]))


	v_tag = sh(['docker', 'volume', 'create'], stdout=subprocess.PIPE).stdout.strip()
	c_tag = sh(['docker', 'run', '-d', '-v', f'{v_tag}:/root', '-w', '/root', 'alpine:latest', 'sh', 'gen_iso.sh'], stdout=subprocess.PIPE).stdout.strip()

	for f in os.listdir(tmp_dir):
		sh(['docker', 'cp', os.path.join(tmp_dir, f), f'{c_tag}:/root'])

	sh(['docker', 'start', '-a', c_tag])
	sh(['docker', 'cp', f'{c_tag}:/root/seed.iso', '.'])
	sh(['docker', 'container', 'rm', c_tag])
	sh(['docker', 'volume', 'rm', v_tag])


def parse_flags():
	import argparse
	available_images = list(SCRIPT_CONFIG['images'].keys())

	parser = argparse.ArgumentParser()
	parser.add_argument('--memory', default='4G')
	parser.add_argument('--nof-cpu', default=4)
	parser.add_argument('--disk-size', default='20G')
	parser.add_argument('--base-image', choices=available_images, default='ubuntu-server-arm64')
	parser.add_argument('vm_name', metavar='NAME')
	parser.add_argument('--ssh-port', default=2225)

	args = parser.parse_args()

	img_cnf = img_config(args.base_image)
	cnf =  Config(
		memory=args.memory,
		cpus=args.nof_cpu,
		image_path=os.path.join(ISO_PATH, args.base_image),
		image=args.base_image,
		disk_path=os.path.join(DISK_PATH, args.vm_name + '.qcow2'),
		disk_size=args.disk_size,
		arch=img_cnf['arch'],
		ssh_port=args.ssh_port,
	)

	return cnf


def main():
	cnf = parse_flags()
	setup(cnf)
	if os.stat(cnf.disk_path).st_size < 200000:
		bootstrap(cnf)
	else:
		start(cnf)

if __name__ == '__main__':
	main()
