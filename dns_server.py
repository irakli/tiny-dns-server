from socket import *
from easyzone import easyzone
import binascii
import io
import os
import struct
import ipaddress
import random

ID = 0
FLAGS = 1
QDCOUNT = 2
ANCOUNT = 3
NSCOUNT = 4
ARCOUNT = 5

RECORDS = {
    1: 'A',
    2: 'NS',
    5: 'CNAME',
    6: 'SOA',
    15: 'MX',
    16: 'TXT',
    28: 'AAAA'
}

ROOT_SERVERS = [
    '198.41.0.4',
    '192.228.79.201',
    '192.33.4.12',
    '199.7.91.13',
    '192.203.230.10',
    '192.5.5.241',
    '192.112.36.4',
    '198.97.190.53',
    '192.36.148.17',
    '192.58.128.30',
    '193.0.14.129',
    '199.7.83.42',
    '202.12.27.33'
]

# Global variables
HEADERS = None
RECURSION_DESIRED = None

def get_bit(byte, index):
	return (byte & 2**index) != 0


def set_bit(byte, index):
	return byte | (2**index)


def clear_bit(byte, index):
	return byte & ~(2**index)


def get_key(dictionary, search_value):
	for key, value in dictionary.items():
		if value == search_value:
			return key


def decompress(domain, message):
	decompressed_domain = ''
	while True:
		first_byte = struct.unpack('!B', domain[:1])
		if get_bit(first_byte[0], 7) and get_bit(first_byte[0], 6):
			pointer = struct.unpack('!H', domain[:2])[0]
			pointer = clear_bit(pointer, 15)
			pointer = clear_bit(pointer, 14)
			decompressed_domain += decompress(message[pointer:], message)
			break
		else:
			text_length = struct.unpack('!B', domain[:1])[0]
			domain = domain[1:]
			part = struct.unpack('!{}c'.format(text_length), domain[:text_length])
			for ch in part:
				decompressed_domain += ch.decode()
			domain = domain[text_length:]

			val = struct.unpack('!B', domain[:1])[0]
			if val == 0:
				break

			decompressed_domain += '.'
			text_length = val

	return decompressed_domain


def find_recursively(requested_domain, message):
	root_server = ROOT_SERVERS[random.randint(0, len(ROOT_SERVERS) - 1)]
	return recursion(requested_domain, message, root_server)


def recursion(requested_domain, message, server):
	send_socket = socket(AF_INET, SOCK_DGRAM)
	send_socket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
	send_socket.bind(('', random.randint(50000, 55000)))
	send_socket.sendto(message, (server, 53))
	reply = send_socket.recvfrom(512)[0]

	headers = struct.unpack('!6H', reply[:12])
	flags = headers[FLAGS]
	answer_count = headers[ANCOUNT]
	nameserver_count = headers[NSCOUNT]
	additional_count = headers[ANCOUNT]

	# Check if the server is authoritive.
	if not get_bit(flags, 10):
		answer_position = 16 + len(domain_to_bytes(requested_domain))
		length = struct.unpack("!H", reply[answer_position + 10: answer_position + 12])[0]
		domain = reply[answer_position + 12 : answer_position + 12 + length]
		decompressed_domain = decompress(domain, message)
		print("{} is not authoritive for {}, continuing recursive search on {}".format(server, requested_domain, decompressed_domain))		
		return recursion(requested_domain, message, decompressed_domain)
	else:
		in_bytes = domain_to_bytes(requested_domain)
		answer_start = reply[16 + len(in_bytes):]
		print("Found authoritive server for {}: {}".format(requested_domain, server))
		return struct.pack('!6H', headers[ID], headers[FLAGS], HEADERS[QDCOUNT], answer_count, nameserver_count, additional_count) + reply[12:16 + len(in_bytes)], answer_start

	return b'', b''

def generate_header(questions):
	# Set correct values for flags
	flags = HEADERS[FLAGS]
	flags = set_bit(flags, 15)  		# Set the query type to answer
	flags = set_bit(flags, 7)  			# Set the recursion available to true
	flags = clear_bit(flags, 5)  		# Clear the AD bit

	return struct.pack('!6H', HEADERS[ID], flags, 1, questions, 0, 0)


def domain_to_bytes(domain):
	split_domain = domain.split('.')
	compressed = b''
	for part in split_domain:
		compressed += struct.pack('!B', len(part)) + str.encode(part)
	compressed += b'\x00' # String terminator

	return compressed


def generate_body(requested_domain, requested_record, zone):
	results = zone.root.records(requested_record).items
	body = b''
	for i in range(0, len(results)):
		compressed = domain_to_bytes(requested_domain)
		record = get_key(RECORDS, requested_record)
		class_type = 1
		ttl = zone.names[requested_domain + '.'].ttl

		answer = b''
		# Answer calculations
		result = zone.root.records(requested_record).items[i]
		if requested_record == 'A':
			answer = inet_pton(AF_INET, result)
		elif requested_record == 'AAAA':
			answer = inet_pton(AF_INET6, result)
		elif requested_record == 'NS':
			answer = domain_to_bytes(result[:len(result) - 1])
		elif requested_record == 'MX':
			answer = struct.pack('!H', result[0])
			answer += domain_to_bytes(result[1][:len(result[1]) - 1])
		elif requested_record == 'TXT':
			result = result[1:len(result) - 1]
			answer += struct.pack('!B', len(result))
			answer += str.encode(result)
		elif requested_record == 'SOA':
			soa = result.split(' ')
			primary_ns = domain_to_bytes(soa[0][:len(soa[0]) - 1])
			mailbox = domain_to_bytes(soa[1][:len(soa[0]) - 1])
			rest = struct.pack('!5I', int(soa[2]), int(soa[3]), int(soa[4]), int(soa[5]), int(soa[6]))
			answer = primary_ns + mailbox + rest

		data_length = len(answer)
		body += compressed + struct.pack('!2HIH', record, class_type, ttl, data_length) + answer

	return body

def generate_query(requested_domain, requested_record, question_query, message):
	path = os.sys.argv[1]
	files = list()

	for filename in os.listdir(path):
		files.append(filename)

	print("Trying to find {} record on {}".format(requested_record, requested_domain))

	try:
		zone = easyzone.zone_from_file(requested_domain, '{}/{}'.format(path, requested_domain + '.conf'))
		if requested_record == 'CNAME':
				requested_record = 'SOA'
		header = generate_header(len(zone.root.records(requested_record).items))
		body = question_query + generate_body(requested_domain, requested_record, zone)
		print("Found {} record locally".format(requested_record))
	except:
		header, body = find_recursively(requested_domain, message)

	return header, body


def parse_body(dns_body):
	"""Parses DNS question from binary data."""

	length = struct.unpack('!B', dns_body[:1])[0]
	dns_body = dns_body[1:]

	requested_domain = ''
	query_length = 0
	while True:
		query_length += length + 1
		part = struct.unpack('!{}c'.format(length), dns_body[:length])
		for ch in part:
			requested_domain += ch.decode()
		dns_body = dns_body[length:]

		val = struct.unpack('!B', dns_body[:1])[0]
		if val == 0:
			query_length += 1
			break

		requested_domain += '.'
		dns_body = dns_body[1:]
		length = val

	record = struct.unpack('!H', dns_body[1:3])[0]
	requested_record = RECORDS[record]

	return requested_domain, requested_record, query_length + 4


def parse_header(dns_header):
	"""Parses DNS header from binary data."""

	global HEADERS
	global RECURSION_DESIRED

	HEADERS = struct.unpack('!6H', dns_header)
	RECURSION_DESIRED = get_bit(HEADERS[FLAGS], 8)


def listener(address):
	"""Listens to the incoming connections."""

	listen_socket = socket(AF_INET, SOCK_DGRAM)
	listen_socket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
	listen_socket.bind(address)

	while True:
		message, client_address = listen_socket.recvfrom(512)

		parse_header(message[:12])
		requested_domain, requested_record, query_length = parse_body(message[12:])

		header, body = generate_query(requested_domain, requested_record, message[12:12 + query_length], message)
		listen_socket.sendto(header + body, client_address)


if __name__ == '__main__':
	if len(os.sys.argv) < 2:
		print("Exiting")
		os.sys.exit()

	listener(('127.0.0.1', 53))
