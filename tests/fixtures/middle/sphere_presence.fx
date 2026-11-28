float4x4 WVP:WORLDVIEWPROJECTION;
bool use_spheremap; bool spadd; bool use_texture;
struct O { float4 p:POSITION; };
O VS(float4 p:POSITION) { O o; o.p=mul(p,WVP);return o; }
float4 PS(O i):COLOR { return float4(use_spheremap,spadd,use_texture,1); }
technique Main < string MMDPass="object"; > { pass P { VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 PS(); } }
