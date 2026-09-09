import html
import unittest
from unittest.mock import patch

from wxManager import MessageType
from wxManager.parser.link_parser import _parse_merged_xml, parser_merged_messages


class MergedXmlTest(unittest.TestCase):
    def item(self, content='message', extra=''):
        return ('<dataitem datatype="1"><datadesc>' + content + '</datadesc>'
                '<sourcename>Sender</sourcename><sourcetime>2026-06-21 12:00:00</sourcetime>'
                '<srcMsgCreateTime>1782014400</srcMsgCreateTime>' + extra + '</dataitem>')

    def record(self, items):
        return f'<recordinfo><datalist count="{len(items)}">' + ''.join(items) + '</datalist></recordinfo>'

    def wrap(self, record, url='https://example.test/?a=1&amp;b=2', cdata=True):
        content = '<![CDATA[' + record + ']]>' if cdata else html.escape(record)
        return ('<msg><appmsg><title>Forwarded messages</title><url>' + url + '</url>'
                '<recorditem>' + content + '</recorditem></appmsg></msg>')

    def parse(self, xml):
        with patch('wxManager.parser.link_parser.logger.error') as error:
            result = parser_merged_messages(xml, '', 'group@chatroom', 1782014400)
        error.assert_not_called()
        return result['messages']

    def test_bare_url_ampersands_do_not_expand_quoted_xml(self):
        quoted = 'account:\n<?xml version="1.0"?><msg><img md5="abc" /></msg>'
        reference = ('<refermsgitem><content>' + html.escape(quoted) + '</content>'
                     '<referdesc>quoted image</referdesc></refermsgitem>')
        items = [self.item(str(i)) for i in range(92)]
        items[50] = self.item('reply', reference)
        xml = self.wrap(self.record(items), url='https://example.test/?a=1&b=2&c=3')
        result = self.parse(xml)
        self.assertEqual(len(result), 92)
        self.assertEqual(result[50].content, 'reply\nquoted image')
        outer = _parse_merged_xml(xml)
        inner = _parse_merged_xml(outer['msg']['appmsg']['recorditem'])
        self.assertEqual(inner['recordinfo']['datalist']['dataitem'][50]['refermsgitem']['content'], quoted)

    def test_valid_entities_are_preserved_when_another_character_is_invalid(self):
        text = '&#20013;&#25991; &#x1F600; &lt;tag&gt; &#38; &amp;lt; &quot;q&quot; &apos;s&apos;'
        expected = '\u4e2d\u6587 \U0001f600 <tag> & &lt; "q" \'s\''
        xml = self.wrap(self.record([self.item(text + '&#1;&#x0B;\x02')]))
        self.assertEqual(self.parse(xml)[0].content, expected)

    def test_bare_ampersand_in_inner_text_is_repaired(self):
        xml = self.wrap(self.record([self.item('A&B &amp; C &copy;')]))
        self.assertEqual(self.parse(xml)[0].content, 'A&B & C &copy;')

    def test_cdata_text_is_preserved_during_inner_xml_repair(self):
        text = '<![CDATA[A&B <tag> &lt;literal&gt;]]>'
        record = self.record([self.item(text), self.item('C&D')])
        result = self.parse(self.wrap(record, cdata=False))
        self.assertEqual([m.content for m in result], ['A&B <tag> &lt;literal&gt;', 'C&D'])

    def test_nested_records_are_parsed_at_their_own_xml_layer(self):
        inner = self.record([self.item('A&B &lt;tag&gt;')])
        nested = ('<dataitem datatype="17"><sourcetime>2026-06-21 12:00:00</sourcetime>'
                  '<srcMsgCreateTime>1782014400</srcMsgCreateTime><recordxml>'
                  + html.escape(inner) + '</recordxml></dataitem>')
        result = self.parse(self.wrap(self.record([nested]), url='https://example.test/?a=1&b=2'))
        self.assertEqual(result[0].type, MessageType.MergedMessages)
        self.assertEqual(result[0].messages[0].content, 'A&B <tag>')

    def test_fully_escaped_document_remains_supported(self):
        xml = self.wrap(self.record([self.item('&lt;literal&gt; &amp;')]))
        self.assertEqual(self.parse(html.escape(xml))[0].content, '<literal> &')

    def test_broken_structure_is_reported_without_dumping_conversation(self):
        xml = '<msg><appmsg><title>private conversation</title>'
        with patch('wxManager.parser.link_parser.logger.error') as error:
            result = parser_merged_messages(xml, '', 'group@chatroom', 1782014400)
        self.assertEqual(result['messages'], [])
        logged = error.call_args.args[0]
        self.assertIn('group@chatroom', logged)
        self.assertIn('1782014400', logged)
        self.assertNotIn('private conversation', logged)


if __name__ == '__main__':
    unittest.main()
