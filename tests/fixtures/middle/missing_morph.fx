// Save as CP932 for native MME; the shared parser accepts UTF-8 or CP932.
float4x4 WVP:WORLDVIEWPROJECTION;
float Missing:CONTROLOBJECT < string name="(self)"; string item="__ninesync_absent_morph__"; > = .37;
float PositiveJP:CONTROLOBJECT < string name="(self)"; string item="まばたき"; >;
float PositiveEN:CONTROLOBJECT < string name="(self)"; string item="Null_88"; >;
struct O { float4 p:POSITION; };
O VS(float4 p:POSITION) { O o;o.p=mul(p,WVP);return o; }
float4 PS(O i):COLOR { return float4(.25+.5*Missing,.25+.5*PositiveJP,.25+.5*PositiveEN,1); }
technique Main < string MMDPass="object"; > {
 pass P { VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 PS(); }
}
