#! /usr/bin/python3

import argparse
from getpass import getpass
import base64
import sys
import binascii
import json
import os

from impacket.examples.utils import parse_credentials, parse_target
from impacket.examples import logger
import logging
from impacket.uuid import string_to_bin, bin_to_string

from .ldap import connect_ldap, get_base_dn, search_ldap, ldap_results, security_descriptor_control, SR_SECURITY_DESCRIPTOR, ACCESS_ALLOWED_OBJECT_ACE, ACCESS_ALLOWED_ACE, ACE
from .response import Response
from .sid import KNOWN_SIDS, name_from_sid
from .logoutput import logoutput
import traceback



show_banner = '''

          _____
         |A .  | _____
         | /.\ ||A ^  | _____
         |(_._)|| / \ ||A _  | _____
         |  |  || \ / || ( ) ||A_ _ |
         |____V||  .  ||(_'_)||( v )|
                |____V||  |  || \ / |
                       |____V||  .  |
                              |____V|

			 Parse and log a target principal's DACL.
							@garrfoster
                               
'''


def arg_parse():
	parser = argparse.ArgumentParser(add_help=True, description="Tool to enumerate a single target's DACL in Active Directory")

	auth_group = parser.add_argument_group("Authentication")
	search_group = parser.add_argument_group("Search target")
	optional_group = parser.add_argument_group("Optional Flags")

	auth_group.add_argument(
		'target',
		action='store',
		help='[[domain/username[:password]@]<address>',
		type=target_type
		)

	optional_group.add_argument(
		"-dc-ip",
		help = "IP address of domain controller",
		required=False
		)
	optional_group.add_argument(
		"-k", "--kerberos",
		action="store_true",
        help='Use Kerberos authentication. Grabs credentials from ccache file '
        '(KRB5CCNAME) based on target parameters. If valid credentials cannot be found, it will use the '
        'ones specified in the command line'
    	)
	
	optional_group.add_argument(
		"-n", "--no-pass",
		action="store_true",
		help="don't ask for password (useful for -k)"
    )
	
	optional_group.add_argument(
		"--hashes",
		metavar="LMHASH:NTHASH",
		help="LM and NT hashes, format is LMHASH:NTHASH",
    )

	optional_group.add_argument(
    	'--aes',
    	action="store",
    	metavar="hex key",
    	help='AES key to use for Kerberos Authentication (128 or 256 bits)'
    	)

	optional_group.add_argument(
		"-scheme",
		help="LDAP scheme to bind to (ldap/ldaps). Aced defaults to ldaps.",
		required=False
		)
	
	optional_group.add_argument(
		"-debug",
		help="LDAP scheme to bind to (ldap/ldaps). Aced defaults to ldaps.",
		required=False
		)


	if len(sys.argv) == 1:
		parser.print_help()
		sys.exit(1)

	#parse auth
	args = parser.parse_args()
	args.userdomain = args.target[0]
	args.username = args.target[1]
	args.password = args.target[2]
	args.address = args.target[3]

	args.lmhash = ""
	args.nthash = ""
	if args.hashes:
		args.lmhash, args.nthash = args.hashes.split(':')

	if not (args.password or args.lmhash or args.nthash or args.aes or args.no_pass):
		args.password = getpass("Password:")

	if args.scheme:
		if args.scheme not in ["ldap", "ldaps"]:
			print("Invalid scheme selection.")
			sys.exit()
		else:
			pass

	return args

def target_type(target):
    domain, username, password, address = parse_target(target)

    if username == "":
        raise argparse.ArgumentTypeError("Username must be specified")

    if domain == "":
        raise argparse.ArgumentTypeError(
            "Domain of user '{}' must be specified".format(username)
        )

    if address == "":
        raise argparse.ArgumentTypeError(
            "Target address (hostname or IP) must be specified"
        )

    return domain, username, password, address


def target_creds_type(target):
    (userdomain, username, password) = parse_credentials(target)

    if username == "":
        raise argparse.ArgumentTypeError("Username should be be specified")

    if userdomain == "":
        raise argparse.ArgumentTypeError(
            "Domain of user '{}' should be be specified".format(username)
        )

    return (userdomain, username, password or '', '')

def fetch_users(ldap_conn, domain, samaccountname, logs_dir):
	user_filter = "(sAMAccountName={})".format(samaccountname)
	search_base = "{}".format(get_base_dn(domain))
	resp = search_ldap(
		ldap_conn,
		user_filter,
		search_base,
		controls = security_descriptor_control(sdflags=0x07))

	for item in ldap_results(resp):
		# get_formatted_value(item)
		logger = logoutput(item, logs_dir)
		logger.query()
		user = Response()
		for attribute in item['attributes']:
			at_type=str(attribute['type'])
			if at_type == 'sAMAccountName':
				user.samaccountname = str(attribute['vals'][0])
			elif at_type == 'description':
				user.description = str(attribute['vals'][0])
			elif at_type == 'nTSecurityDescriptor':
				secdesc = (attribute['vals'][0].asOctets())
				user.security_descriptor.fromString(secdesc)
			elif at_type == 'dNSHostName':
				user.dnshostname = str(attribute['vals'][0])
			elif at_type == 'objectSid':
				user.objectsid = (attribute['vals'][0])
			elif at_type == 'memberOf':
				x = str(attribute['vals'])
				y = "".join(x.splitlines())
				z = y.replace('SetOf: ', '').replace(' CN=','\nCN=')
				user.members = z
			elif at_type == 'member':
				x = str(attribute['vals'])
				y = "".join(x.splitlines())
				z = y.replace('SetOf: ', '').replace(' CN=','\nCN=')
				user.members = z

		yield user


def resolve_key():
	args = arg_parse()
	ldap_conn = connect_ldap(
		domain=args.userdomain,
		user=args.username,
		password=args.password,
		dc_ip=args.address,
		scheme=args.scheme
	)

	domain = args.userdomain
	guid_filter = "(cn=ms-DS-Key-Credential-Link)"
	search_base = "CN=Schema,CN=Configuration,{}".format(get_base_dn(domain))
	
	resp = search_ldap(
		ldap_conn,
		guid_filter,
		search_base)

	for item in ldap_results(resp):
		for attribute in item['attributes']:
			at_type=str(attribute['type'])
			if at_type == 'schemaIDGUID':
				guid = guid_to_string(attribute['vals'][0])
		return guid


def print_user(user, sids_resolver):
	print ("Name: {}".format(user.samaccountname))
	if len(user.description) > 0:
		print ("Description: {}".format(user.description))
	if len(user.dnshostname) > 0:
		print ("DNS Hostname: {}".format(user.dnshostname))
	owner_sid = user.owner_sid.formatCanonical()
	owner_domain, owner_name = sids_resolver.get_name_from_sid(owner_sid)
	print("Owner SID: {} {}\{}".format(user.owner_sid.formatCanonical(), owner_domain, owner_name))
	print("Member of: {}".format(user.memberOf))
	print ("Group members:\n{}".format(user.members))

	#write perms
	write_owner_sids = set()
	write_dacl_sids = set()
	writespn_property_sids = set()
	writekeycred_property_sids = set()
	addself_property_sids = set()
	writemember_property_sids = set()
	allowedtoact_property_sids = set()

	#generic perms
	genericall_property_sids = set()
	genericwrite_property_sids = set()

	# Extended perms
	changepass_property_sids = set()
	allextended_property_sids = set()
	getchanges_property_sids = set()
	getchanges_all_property_sids = set()

	# Read perms
	readlaps_property_sids = set()

	for ace in user.dacl.aces:
		#ACE type 0x05
		if ace["TypeName"] == "ACCESS_ALLOWED_OBJECT_ACE":
			ace = ace["Ace"]
			mask = ace["Mask"]
			sid = ace["Sid"].formatCanonical()

			if ace.hasFlag(ace.ACE_OBJECT_TYPE_PRESENT):
				#check generics first
				if mask.hasPriv(ACCESS_MASK.GENERIC_ALL):
					genericall_property_sids.add(sid)
				elif mask.hasPriv(ACCESS_MASK.GENERIC_WRITE):
					genericwrite_property_sids.add(sid)
				elif mask.hasPriv(ACCESS_MASK.WRITE_OWNER):
					write_owner_sids.add(sid)
				elif mask.hasPriv(ACCESS_MASK.WRITE_DACL):
					write_owner_sids.add(sid)

				# ForceChangePassword
				elif guid_to_string(ace["ObjectType"]) == FORCE_CHANGE_PASSWORD:
					changepass_property_sids.add(sid)
				# getchanges
				elif guid_to_string(ace["ObjectType"]) == GET_CHANGES:
					getchanges_property_sids.add(sid)
				# getchangesall
				elif guid_to_string(ace["ObjectType"]) == GET_CHANGES_ALL:
					getchanges_all_property_sids.add(sid)
				elif mask.hasPriv(ace.ADS_RIGHT_DS_WRITE_PROP):
					#whisker
					if guid_to_string(ace["ObjectType"]) == WRITE_KEY:
						writekeycred_property_sids.add(sid)
					#targeted kerberoast
					elif guid_to_string(ace["ObjectType"]) == WRITE_SPN:
						writespn_property_sids.add(sid)
					# add user to group
					elif guid_to_string(ace["ObjectType"]) == WRITE_MEMBER:
						writemember_property_sids.add(sid)
					#RBCD
					elif guid_to_string(ace["ObjectType"]) == ALLOWED_TO_ACT:
						allowedtoact_property_sids.add(sid)
				# elif mask.hasPriv(ace.ADS_RIGHT_DS_READ_PROP):
				# 	if guid_to_string(ace["ObjectType"]) == READ_LAPS:
				# 		readlaps_property_sids.add(sid)

				# add self to group
				elif mask.hasPriv(ace.ADS_RIGHT_DS_SELF):
					if guid_to_string(ace["ObjectType"]) == WRITE_MEMBER:
						addself_property_sids.add(sid)
			# empty objecttype but ADS_RIGHT true means it applies to all objects
			if not ace.hasFlag(ace.ACE_OBJECT_TYPE_PRESENT):
				# all extended rights
				if mask.hasPriv(ace.ADS_RIGHT_DS_CONTROL_ACCESS):
					allextended_property_sids.add(sid)
				# generic write
				elif mask.hasPriv(ace.ADS_RIGHT_DS_WRITE_PROP):
					genericwrite_property_sids.add(sid)

		#ACE type 0x00
		elif ace["TypeName"] == "ACCESS_ALLOWED_ACE":
			ace = ace["Ace"]
			mask = ace["Mask"]
			sid = ace["Sid"].formatCanonical()
			if mask.hasPriv(ACCESS_MASK.GENERIC_ALL):
				genericall_property_sids.add(sid)
			if mask.hasPriv(ACCESS_MASK.GENERIC_WRITE):
				genericwrite_property_sids.add(sid)
			if mask.hasPriv(ACCESS_MASK.WRITE_OWNER):
				write_owner_sids.add(sid)
			if mask.hasPriv(ACCESS_MASK.WRITE_DACL):
				write_dacl_sids.add(sid)
			if mask.hasPriv(ACCESS_MASK.ADS_RIGHT_DS_CONTROL_ACCESS):
				allextended_property_sids.add(sid)

		# else:
		# 	continue

	print("  Interesting Permissions:")
	print("    Principals that can change target's password:")
	if len(changepass_property_sids) > 0:
		print_sids(changepass_property_sids, sids_resolver, offset=6)
	else:
		print("      No entries found.")

	print("    Principals that can modify the SPN attribute:")
	if len(writespn_property_sids) > 0:
		print_sids(writespn_property_sids, sids_resolver, offset=6)
	else:
		print("      No entries found.")

	print("    Principals that can modify the msDS-KeyCredentialLink attribute:")
	if len(writekeycred_property_sids) > 0:
		print_sids(writekeycred_property_sids, sids_resolver, offset=6)
	else:
		print("      No entries found.")

	print("    Principals with AllExtendedRights:")
	if len(allextended_property_sids) > 0:
		print_sids(allextended_property_sids, sids_resolver, offset=6)
	else:
		print("      No entries found.")
	print("")

	# DCSYNC
	if (len(getchanges_property_sids) > 0) or (len(getchanges_all_property_sids) > 0):
		print("  DCSYNC Rights:")
		print("    Principals with GetChanges:")
		if len(getchanges_property_sids) > 0:
			print_sids(getchanges_property_sids, sids_resolver, offset=6)
		if len(getchanges_all_property_sids) > 0:
			print("    Principals with GetChangesAll:")
		print_sids(getchanges_all_property_sids, sids_resolver, offset=6)
	print ("")

	# write permissions
	print("  Write Permissions:")
	print("    Principals with Write Owner:")
	print_sids(write_owner_sids, sids_resolver, offset=6)

	print("    Principals with write DACL:")
	print_sids(write_dacl_sids, sids_resolver, offset=6)
	print("")

	# group permissionsa
	if (len(writemember_property_sids) > 0) or (len(addself_property_sids) > 0):
		print("  Group Permissions:")
		print("   Principals that can add members to group:")
		if len(writemember_property_sids) > 0:
			print_sids(writemember_property_sids, sids_resolver, offset=6)
		else: print("      No entries found.")
		print("    Principals that can add themself to group:")
		if len(addself_property_sids) > 0:
			print_sids(addself_property_sids, sids_resolver, offset=6)
		else: print("      No entries found.")
	print("")

	# generic permissions
	if (len(genericwrite_property_sids) > 0) or (len(genericall_property_sids) > 0):
		print("  Generic Permissions:")
		print("    Principals with Generic Write:")
		if len(genericwrite_property_sids) > 0:
			print_sids(genericwrite_property_sids, sids_resolver, offset=6)
		else:
			print("      No entries found.")
		print("    Principals with Generic All:")
		if len(genericall_property_sids) > 0:
			print_sids(genericall_property_sids, sids_resolver, offset=6)
		else:
			print("      No entries found.")
	print("")

def print_sids(sids, sids_resolver, offset=0):
	blanks = " " * offset
	msg = []
	ignoresids = ["S-1-3-0", "S-1-5-18", "S-1-5-10", "S-1-1-0"]
	for sid in sids:
		if sid not in ignoresids:
			domain, name = sids_resolver.get_name_from_sid(sid)
			msg.append("{} {}\{}".format(sid, domain, name))
	print("\n".join(["{}{}".format(blanks, line) for line in msg]))

def guid_to_string(guid):
    return "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}".format(
        guid[3], guid[2], guid[1], guid[0],
        guid[5], guid[4],
        guid[7], guid[6],
        guid[8], guid[9],
        guid[10], guid[11], guid[12], guid[13], guid[14], guid[15]
    )

def ldap_get_name_from_sid(ldap_conn, sid):
    if type(sid) is not str:
        sid = sid.formatCanonical()

    sid_filter = "(objectsid={})".format(sid)
    resp = search_ldap(ldap_conn, sid_filter)

    for item in ldap_results(resp):
        for attribute in item['attributes']:
            if str(attribute["type"]) == "sAMAccountName":
                name = str(attribute["vals"][0])
                return name

def ldap_get_domain_from_sid(ldap_conn, sid):
    if type(sid) is not str:
        sid = sid.formatCanonical()

    sid_filter = "(objectsid={})".format(sid)
    resp = search_ldap(ldap_conn, sid_filter)

    for item in ldap_results(resp):
        for attribute in item['attributes']:
            at_type = str(attribute["type"])
            if at_type == "name":
                return str(attribute["vals"][0])

                name = ".".join([x.lstrip("DC=") for x in value.split(",")])
                return


def bofhound_logging():
	# check for first time usage
	home = os.path.expanduser('~')
	aced_dir = f'{home}/.aced'
	logs_dir = f'{aced_dir}/logs'

	if not os.path.isdir(aced_dir):
		logging.info('First time usage detected')
		logging.info(f'aced output will be logged to {logs_dir}')
		os.mkdir(aced_dir)
		print()

	if not os.path.isdir(logs_dir):
		os.mkdir(logs_dir)
	return logs_dir

#objecttypes
FORCE_CHANGE_PASSWORD = "00299570-246d-11d0-a768-00aa006e0529"
WRITE_SPN = "f3a64788-5306-11d1-a9c5-0000f80367c1"
#WRITE_KEY = resolve_key()
GET_CHANGES = "1131f6aa-9c07-11d1-f79f-00c04fc2dcd2"
GET_CHANGES_ALL = "1131f6ad-9c07-11d1-f79f-00c04fc2dcd2"
ALLOWED_TO_ACT = "3f78c3e5-f79a-46bd-a0b8-9d18116ddc79"
WRITE_MEMBER = "bf9679c0-0de6-11d0-a285-00aa003049e2"

def main():

	logger.init()
	args = arg_parse()
	
	if args.debug:
		logging.getLogger().setLevel(logging.DEBUG)
		logging.debug(version.getInstallationPath())
	else:
		logging.getLogger().setLevel(logging.INFO)

	ldap_conn = connect_ldap(
		domain=args.userdomain,
		user=args.username,
		password=args.password,
		lmhash=args.lmhash,
		nthash=args.nthash,
		aesKey=args.address,
		dc_ip=args.address,
		kerberos=args.kerberos,
		scheme=args.scheme
	)
	# base_dn = get_base_dn
	sids_resolver = SidsResolver(ldap_conn)
	domain = args.userdomain
	print (show_banner)
	logs_dir = bofhound_logging()
	samaccountname = input ("Enter target sAMAccountName: ")
	while True:
		if samaccountname == "quit":
			logging.info(f'User entered quit. Stopping session.')
			logging.info(f'Results written to {logs_dir}')
			break
		test=list(fetch_users(ldap_conn, domain, samaccountname, logs_dir))


		if not test:
			logging.info(f'Target {samaccountname} not found.')
			samaccountname = input ("Enter new sAMAccountName to search or enter quit to stop: ")
		else:
			for user in test:
				print_user(user, sids_resolver)
				samaccountname = input ("Enter new sAMAccountName to search or enter quit to stop: ")


class SidsResolver:

    def __init__(self, ldap_conn):
        self.ldap_conn = ldap_conn
        self.cached_sids = {}
        self.domain_sids = {}

    def get_name_from_sid(self, sid):
        if type(sid) is not str:
            sid = sid.formatCanonical()

        try:
            return ("BUILTIN", KNOWN_SIDS[sid])
        except KeyError:
            pass

        try:
            return self.cached_sids[sid]
        except KeyError:
            pass

        domain_sid = "-".join(sid.split("-")[:-1])
        domain = self.get_domain_from_sid(domain_sid)


        name = ldap_get_name_from_sid(self.ldap_conn, sid)
        self.cached_sids[sid] = (domain, name)

        return (domain, name)

    def get_domain_from_sid(self, sid):
        try:
            return self.domain_sids[sid]
        except KeyError:
            pass

        name = ldap_get_domain_from_sid(self.ldap_conn, sid)
        self.domain_sids[sid] = name
        return name

class ACCESS_MASK:
    # Flag constants

    # These constants are only used when WRITING
    # and are then translated into their actual rights
    SET_GENERIC_READ        = 0x80000000
    SET_GENERIC_WRITE       = 0x04000000
    SET_GENERIC_EXECUTE     = 0x20000000
    SET_GENERIC_ALL         = 0x10000000
    # When reading, these constants are actually represented by
    # the following for Active Directory specific Access Masks
    # Reference: https://docs.microsoft.com/en-us/dotnet/api/system.directoryservices.activedirectoryrights?view=netframework-4.7.2
    GENERIC_READ            = 0x00020094
    GENERIC_WRITE           = 0x00020028
    GENERIC_EXECUTE         = 0x00020004
    GENERIC_ALL             = 0x000F01FF

    # These are actual rights (for all ACE types)
    MAXIMUM_ALLOWED         = 0x02000000
    ACCESS_SYSTEM_SECURITY  = 0x01000000
    SYNCHRONIZE             = 0x00100000
    WRITE_OWNER             = 0x00080000
    WRITE_DACL              = 0x00040000
    READ_CONTROL            = 0x00020000
    DELETE                  = 0x00010000

    # ACE type specific mask constants (for ACCESS_ALLOWED_OBJECT_ACE)
    # Note that while not documented, these also seem valid
    # for ACCESS_ALLOWED_ACE types
    ADS_RIGHT_DS_CONTROL_ACCESS         = 0x00000100
    ADS_RIGHT_DS_CREATE_CHILD           = 0x00000001
    ADS_RIGHT_DS_DELETE_CHILD           = 0x00000002
    ADS_RIGHT_DS_READ_PROP              = 0x00000010
    ADS_RIGHT_DS_WRITE_PROP             = 0x00000020
    ADS_RIGHT_DS_SELF                   = 0x00000008

    def __init__(self, mask):
        self.mask = mask

    def has_priv(self, priv):
        return self.mask & priv == priv

    def set_priv(self, priv):
        self.mask |= priv

    def remove_priv(self, priv):
        self.mask ^= priv

    def __repr__(self):
        out = []
        for name, value in iteritems(vars(ACCESS_MASK)):
            if not name.startswith('_') and type(value) is int and self.has_priv(value):
                out.append(name)
        return "<ACCESS_MASK RawMask=%d Flags=%s>" % (self.mask, ' | '.join(out))



if __name__ == '__main__':
    main()